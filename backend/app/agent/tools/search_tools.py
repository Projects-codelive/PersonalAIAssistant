import asyncio
import logging
import re
from typing import Dict, Any, Optional, List
from langchain_core.tools import tool
from langchain_community.utilities import WikipediaAPIWrapper, ArxivAPIWrapper, PubMedAPIWrapper
from langchain_community.tools import WikipediaQueryRun, DuckDuckGoSearchRun
from langchain_community.tools.arxiv.tool import ArxivQueryRun
from langchain_community.tools.pubmed.tool import PubmedQueryRun
from app.config import settings

logger = logging.getLogger(__name__)

# Initialize underlying API wrappers and tools safely
try:
    import wikipedia
    wikipedia.set_user_agent("AgentToolAssistant/1.0 (https://agenttool.ai; bot@agenttool.ai)")
    wiki_wrapper = WikipediaAPIWrapper(top_k_results=2, doc_content_chars_max=1200)
    wiki_tool = WikipediaQueryRun(api_wrapper=wiki_wrapper)
except Exception as e:
    logger.warning(f"Could not initialize Wikipedia tool: {e}")
    wiki_tool = None

try:
    ddg_tool = DuckDuckGoSearchRun(name="DuckDuckGoSearch")
except Exception as e:
    logger.warning(f"Could not initialize DuckDuckGo tool: {e}")
    ddg_tool = None

try:
    arxiv_wrapper = ArxivAPIWrapper(top_k_results=1, doc_content_chars_max=1200)
    arxiv_tool = ArxivQueryRun(api_wrapper=arxiv_wrapper)
except Exception as e:
    logger.warning(f"Could not initialize Arxiv tool: {e}")
    arxiv_tool = None

try:
    pubmed_wrapper = PubMedAPIWrapper(top_k_results=1, doc_content_chars_max=1200)
    pubmed_tool = PubmedQueryRun(api_wrapper=pubmed_wrapper)
except Exception as e:
    logger.warning(f"Could not initialize PubMed tool: {e}")
    pubmed_tool = None


@tool
def search_wikipedia(query: str) -> str:
    """Search Wikipedia for definitions, historical background, concepts, places, and people."""
    if not wiki_tool:
        return "Wikipedia tool is not available."
    try:
        return wiki_tool.run(query)
    except Exception as e:
        return f"Wikipedia search failed: {str(e)}"


@tool
def search_duckduckgo(query: str) -> str:
    """Search the web via DuckDuckGo for practical how-tos, tech guides, android options, news, and current events."""
    if not ddg_tool:
        return "DuckDuckGo tool is not available."
    try:
        return ddg_tool.run(query)
    except Exception as e:
        return f"DuckDuckGo search failed: {str(e)}"


@tool
def search_arxiv(query: str) -> str:
    """Search arXiv for scientific research papers, computer science, mathematics, AI, and physics."""
    if not arxiv_tool:
        return "arXiv tool is not available."
    try:
        return arxiv_tool.run(query)
    except Exception as e:
        return f"arXiv search failed: {str(e)}"


@tool
def search_pubmed(query: str) -> str:
    """Search PubMed for biomedical, clinical, medical, and healthcare literature."""
    if not pubmed_tool:
        return "PubMed tool is not available."
    try:
        return pubmed_tool.run(query)
    except Exception as e:
        return f"PubMed search failed: {str(e)}"


SEARCH_TOOLS = [
    search_wikipedia,
    search_duckduckgo,
    search_arxiv,
    search_pubmed,
]


class SearchKnowledgeService:
    """
    Intelligent Information & Knowledge Retrieval Service.
    Instead of opening a web browser on the user's phone, this service:
    1. Calls local/community knowledge tools (Wikipedia, DuckDuckGo, Arxiv, PubMed).
    2. Explicitly specifies on which site it is finding the info and what info it takes.
    3. Sends the raw findings to local Gemma LLM (Ollama gemma:2b).
    4. Reframes and synthesizes the response in the user's language.
    """

    SITE_METADATA = {
        "wikipedia": {
            "name": "Wikipedia",
            "domain": "en.wikipedia.org",
            "tool_name": "search_wikipedia",
            "runner": wiki_tool
        },
        "duckduckgo": {
            "name": "DuckDuckGo Web Search",
            "domain": "duckduckgo.com",
            "tool_name": "search_duckduckgo",
            "runner": ddg_tool
        },
        "arxiv": {
            "name": "arXiv",
            "domain": "arxiv.org",
            "tool_name": "search_arxiv",
            "runner": arxiv_tool
        },
        "pubmed": {
            "name": "PubMed",
            "domain": "pubmed.ncbi.nlm.nih.gov",
            "tool_name": "search_pubmed",
            "runner": pubmed_tool
        }
    }

    @classmethod
    def clean_query(cls, raw: str) -> str:
        """Strip filler words and trigger prefixes from user query"""
        q = raw.strip()
        # Remove colloquial Hindi/Hinglish/English prefixes
        q = re.sub(
            r"^(?:yaar|bhai|please|zara)?\s*(?:google|chrome|browser|internet|net\s*pe)?\s*(?:pe|par|me|on)?\s*(?:search|karo|dekh|dekho|batao|find)?\s*",
            "",
            q,
            flags=re.I
        )
        q = re.sub(
            r"\b(google|chrome|browser|search|on|in|karo|me|pe|par|dekh|ke|batao|please|yaar|bhai|tell\s+me|find)\b",
            "",
            q,
            flags=re.I
        ).strip()
        return q or raw.strip()

    @classmethod
    def select_source(cls, user_msg: str, clean_q: str, explicit_source: Optional[str] = None) -> str:
        """Select appropriate information source based on user message content"""
        if explicit_source and explicit_source.lower() in cls.SITE_METADATA:
            return explicit_source.lower()

        m = user_msg.lower()

        # Explicit tool mentions
        if "arxiv" in m or "research paper" in m or "scientific paper" in m:
            return "arxiv"
        if "pubmed" in m or "medical" in m or "clinical" in m or "disease" in m or "biology" in m:
            return "pubmed"
        if "wiki" in m or "wikipedia" in m:
            return "wikipedia"

        # How-to, developer mode, technical settings, procedures -> DuckDuckGo Search
        if "how to" in m or "developer mode" in m or "setting" in m or "steps" in m or "tutorial" in m:
            return "duckduckgo"

        # Definition/encyclopedic background -> Wikipedia
        if any(m.startswith(w) for w in ["what is", "who is", "who was", "where is", "define", "history of", "kya hai", "kaun hai"]):
            return "wikipedia"

        # Default general search
        return "duckduckgo"

    @classmethod
    async def fetch_info(cls, source_key: str, query: str) -> Dict[str, Any]:
        """Fetch information from the designated tool"""
        meta = cls.SITE_METADATA.get(source_key, cls.SITE_METADATA["duckduckgo"])
        runner = meta["runner"]
        site_name = f"{meta['name']} ({meta['domain']})"

        if not runner:
            return {
                "source_key": source_key,
                "site": site_name,
                "query": query,
                "info": f"Tool '{meta['name']}' is not available.",
                "success": False
            }

        try:
            logger.info(f"Querying {site_name} for '{query}'...")
            if hasattr(runner, "ainvoke"):
                raw_info = await runner.ainvoke(query)
            elif hasattr(runner, "arun"):
                raw_info = await runner.arun(query)
            else:
                raw_info = runner.run(query)

            raw_str = str(raw_info).strip()
            # If no meaningful result from DuckDuckGo or Wikipedia, fallback to the other
            if (not raw_str or "No good DuckDuckGo search result" in raw_str or "No good result" in raw_str) and source_key == "duckduckgo":
                if wiki_tool:
                    logger.info(f"Falling back to Wikipedia for '{query}'...")
                    fallback_info = wiki_tool.run(query)
                    if fallback_info and "No good" not in fallback_info:
                        return {
                            "source_key": "wikipedia",
                            "site": f"Wikipedia ({cls.SITE_METADATA['wikipedia']['domain']})",
                            "query": query,
                            "info": fallback_info.strip(),
                            "success": True
                        }

            return {
                "source_key": source_key,
                "site": site_name,
                "query": query,
                "info": raw_str,
                "success": bool(raw_str)
            }
        except Exception as e:
            logger.error(f"Error fetching from {site_name}: {e}")
            return {
                "source_key": source_key,
                "site": site_name,
                "query": query,
                "info": f"Encountered error while searching: {str(e)}",
                "success": False
            }

    @classmethod
    async def reframe_with_gemma(cls, user_msg: str, site_name: str, query: str, raw_findings: str) -> str:
        """
        Send raw findings to local Gemma LLM to synthesize and reframe a polished,
        natural conversational response clearly citing the source site.
        """
        # Truncate raw findings to keep prompt concise for Gemma
        excerpt = raw_findings[:1500].strip()

        reframe_prompt = f"""You are AgentTool, an intelligent assistant powered by local Gemma.
The user asked: "{user_msg}"

The tool fetched the following information from {site_name} for query "{query}":
\"\"\"{excerpt}\"\"\"

Task:
1. Reframe and synthesize the findings into a clear, helpful, accurate answer to the user's question.
2. Explicitly cite the site ({site_name}) where you found this information.
3. Keep it natural, concise, and structured (use bullet points if explaining steps).
4. Match the user's language (e.g. English, Hindi, or Hinglish).
"""

        try:
            import ollama
            logger.info(f"Reframing findings with local Gemma ({settings.GEMMA_MODEL_NAME})...")
            from app.agent.gemma_agent import OLLAMA_KEEP_ALIVE
            response = await asyncio.to_thread(
                ollama.Client(host=settings.OLLAMA_BASE_URL).generate,
                model=settings.GEMMA_MODEL_NAME,
                prompt=reframe_prompt,
                think=False,
                keep_alive=OLLAMA_KEEP_ALIVE,
            )
            ans = response.get("response", "").strip()
            if ans:
                return ans
        except Exception as e:
            logger.warning(f"Ollama direct generate failed: {e}. Trying ChatOllama...")
            try:
                from langchain_ollama import ChatOllama
                from langchain_core.messages import SystemMessage, HumanMessage
                llm = ChatOllama(model=settings.GEMMA_MODEL_NAME, base_url=settings.OLLAMA_BASE_URL, reasoning=False)
                resp = await llm.ainvoke([
                    SystemMessage(content="You are a helpful research assistant. Reframe and summarize the provided findings clearly."),
                    HumanMessage(content=reframe_prompt)
                ])
                if resp.content.strip():
                    return resp.content.strip()
            except Exception as e2:
                logger.error(f"ChatOllama reframe also failed: {e2}")

        # Fallback reframing if Ollama is temporarily unavailable
        return f"According to {site_name} for '{query}':\n\n{excerpt}"

    @classmethod
    async def search_and_reframe(
        cls,
        user_msg: str,
        query: str = "",
        explicit_source: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Main entrypoint: executes search via tools (wiki, arxiv, ddg, pubmed)
        and sends findings to Gemma for reframing.
        """
        clean_q = cls.clean_query(query or user_msg)
        source_key = cls.select_source(user_msg, clean_q, explicit_source=explicit_source)
        meta = cls.SITE_METADATA.get(source_key, cls.SITE_METADATA["duckduckgo"])

        # 1. Fetch info
        fetched = await cls.fetch_info(source_key, clean_q)
        site_name = fetched["site"]
        info_taken = fetched["info"]
        actual_source_key = fetched.get("source_key", source_key)
        actual_meta = cls.SITE_METADATA.get(actual_source_key, meta)

        # 2. Build structured tool call report
        executed_tools = [{
            "tool": actual_meta["tool_name"],
            "args": {
                "query": clean_q,
                "site": site_name,
                "domain": actual_meta["domain"]
            },
            "site": site_name,
            "info_taken": info_taken[:800],
            "result": f"Fetched information from {site_name}: {info_taken[:250]}..."
        }]

        # 3. Reframe response with local Gemma
        reframed_reply = await cls.reframe_with_gemma(
            user_msg=user_msg,
            site_name=site_name,
            query=clean_q,
            raw_findings=info_taken
        )

        return {
            "reply": reframed_reply,
            "tool_calls": executed_tools,
            "site": site_name,
            "query": clean_q,
            "info_taken": info_taken,
            "screenshots": [],
            "confirmation_required": False
        }


search_knowledge_service = SearchKnowledgeService()
