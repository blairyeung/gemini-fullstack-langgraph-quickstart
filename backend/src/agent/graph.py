import os
import logging
from datetime import datetime
import time
from duckduckgo_search.exceptions import DuckDuckGoSearchException

logger = logging.getLogger(__name__)

from agent.tools_and_schemas import SearchQueryList, Reflection
from langchain_core.messages import AIMessage
from langgraph.types import Send
from langgraph.graph import StateGraph
from langgraph.graph import START, END
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI

from agent.state import (
    OverallState,
    QueryGenerationState,
    ReflectionState,
    WebSearchState,
)
from agent.configuration import Configuration
from agent.prompts import (
    get_current_date,
    query_writer_instructions,
    web_searcher_instructions,
    reflection_instructions,
    answer_instructions,
)
from agent.utils import (
    get_research_topic,
)

from duckduckgo_search import DDGS


# Nodes
def generate_query(state: OverallState, config: RunnableConfig):
    """LangGraph node that generates search queries based on the User's question.

    Uses local OpenAI model to create an optimized search queries for web research based on
    the User's question.

    Args:
        state: Current graph state containing the User's question
        config: Configuration for the runnable, including LLM provider settings

    Returns:
        Dictionary with state update, including search_query key containing the generated queries
    """
    configurable = Configuration.from_runnable_config(config)

    # check for custom initial search query count
    if state.get("initial_search_query_count") is None:
        state["initial_search_query_count"] = configurable.number_of_initial_queries

    # init local OpenAI model
    llm = ChatOpenAI(
        model=configurable.query_generator_model,
        temperature=1.0,
        max_retries=2,
        base_url="http://localhost:6000/v1",
        api_key="not-needed",
    )
    structured_llm = llm.with_structured_output(SearchQueryList)

    # Format the prompt
    current_date = get_current_date()
    research_topic = get_research_topic(state["messages"])
    formatted_prompt = query_writer_instructions.format(
        current_date=current_date,
        research_topic=research_topic,
        number_queries=state["initial_search_query_count"],
    )
    # Generate the search queries
    result = structured_llm.invoke(formatted_prompt)
    return {"search_query": result.query, "research_topic": research_topic}


def continue_to_web_research(state: OverallState):
    """LangGraph node that sends the search queries to the web research node.

    This is used to spawn n number of web research nodes, one for each search query.
    """
    return [
        Send(
            "web_research",
            {
                "search_query": search_query,
                "research_topic": state["research_topic"],
                "id": int(idx),
            },
        )
        for idx, search_query in enumerate(state["search_query"])
    ]


def web_research(state: WebSearchState, config: RunnableConfig) -> OverallState:
    """LangGraph node that performs web research using the DuckDuckGo Search API tool.

    Executes a web search using the DuckDuckGo Search API tool.

    Args:
        state: Current graph state containing the search query and research loop count
        config: Configuration for the runnable, including search API settings

    Returns:
        Dictionary with state update, including sources_gathered, research_loop_count, and web_research_results
    """
    # Configure
    configurable = Configuration.from_runnable_config(config)
    formatted_prompt = web_searcher_instructions.format(
        current_date=get_current_date(),
        research_topic=state["research_topic"],
    )

    # Convert search results into a structured list (max 5 results)
    search_results = []
    try:
        with DDGS() as ddgs:
            for res in ddgs.text(state["search_query"], max_results=5):
                search_results.append(res)
    except DuckDuckGoSearchException as e:
        logger.warning(f"DuckDuckGo search failed with {e}. Retrying with 'lite' backend.")
        time.sleep(2)  # wait for 2 seconds before retrying
        try:
            with DDGS() as ddgs:
                for res in ddgs.text(
                    state["search_query"], max_results=5, backend="lite"
                ):
                    search_results.append(res)
        except DuckDuckGoSearchException as e2:
            logger.error(f"DuckDuckGo search failed again with {e2}. Giving up.")

    if not search_results:
        logger.warning("No web search results returned for query '%s'", state["search_query"])

    # Build a simple textual summary of the search results
    search_summary_lines = []
    sources_gathered = []
    for idx, res in enumerate(search_results, start=1):
        line = f"{idx}. {res.get('title', '')}: {res.get('body', '')} (URL: {res.get('href', '')})"
        search_summary_lines.append(line)
        sources_gathered.append(res.get("href", ""))

    search_summary = "\n".join(search_summary_lines)

    # init local OpenAI model – use the same model throughout
    llm = ChatOpenAI(
        model=configurable.answer_model,
        temperature=0,
        base_url="http://localhost:6000/v1",
        api_key="not-needed",
    )

    # Combine search result with the prompt
    web_research_prompt = f"{formatted_prompt}\n\nHere are the search results:\n{search_summary}"
    logger.info("[web_research] Prompt sent to LLM:\n%s", web_research_prompt)
    result = llm.invoke(web_research_prompt)

    logger.info("[web_research] LLM response: %s", result.content[:2000])

    return {
        "sources_gathered": sources_gathered,
        "search_query": [state["search_query"]],
        "web_research_result": [result.content],
    }


def reflection(state: OverallState, config: RunnableConfig) -> ReflectionState:
    """LangGraph node that identifies knowledge gaps and generates potential follow-up queries.

    Analyzes the current summary to identify areas for further research and generates
    potential follow-up queries. Uses structured output to extract
    the follow-up query in JSON format.

    Args:
        state: Current graph state containing the running summary and research topic
        config: Configuration for the runnable, including LLM provider settings

    Returns:
        Dictionary with state update, including search_query key containing the generated follow-up query
    """
    configurable = Configuration.from_runnable_config(config)
    # Increment the research loop count and get the reasoning model
    state["research_loop_count"] = state.get("research_loop_count", 0) + 1
    reasoning_model = state.get("reasoning_model", configurable.reflection_model)

    # Format the prompt
    current_date = get_current_date()
    formatted_prompt = reflection_instructions.format(
        current_date=current_date,
        research_topic=state["research_topic"],
        summaries="\n\n---\n\n".join(state["web_research_result"]),
    )
    logger.info("[reflection] Prompt:\n%s", formatted_prompt)
    # init Reasoning Model
    llm = ChatOpenAI(
        model=reasoning_model,
        temperature=1.0,
        max_retries=2,
        base_url="http://localhost:6000/v1",
        api_key="not-needed",
    )
    result = llm.with_structured_output(Reflection).invoke(formatted_prompt)

    return {
        "is_sufficient": result.is_sufficient,
        "knowledge_gap": result.knowledge_gap,
        "follow_up_queries": result.follow_up_queries,
        "research_loop_count": state["research_loop_count"],
        "number_of_ran_queries": len(state["search_query"]),
        "research_topic": state["research_topic"],
    }


def evaluate_research(
    state: ReflectionState,
    config: RunnableConfig,
) -> OverallState:
    """LangGraph routing function that determines the next step in the research flow.

    Controls the research loop by deciding whether to continue gathering information
    or to finalize the summary based on the configured maximum number of research loops.

    Args:
        state: Current graph state containing the research loop count
        config: Configuration for the runnable, including max_research_loops setting

    Returns:
        String literal indicating the next node to visit ("web_research" or "finalize_summary")
    """
    configurable = Configuration.from_runnable_config(config)
    max_research_loops = (
        state.get("max_research_loops")
        if state.get("max_research_loops") is not None
        else configurable.max_research_loops
    )
    if state["is_sufficient"] or state["research_loop_count"] >= max_research_loops:
        return "finalize_answer"
    else:
        return [
            Send(
                "web_research",
                {
                    "search_query": follow_up_query,
                    "research_topic": state["research_topic"],
                    "id": state["number_of_ran_queries"] + int(idx),
                },
            )
            for idx, follow_up_query in enumerate(state["follow_up_queries"])
        ]


def finalize_answer(state: OverallState, config: RunnableConfig):
    """LangGraph node that finalizes the research summary.

    Prepares the final output by deduplicating and formatting sources, then
    combining them with the running summary to create a well-structured
    research report with proper citations.

    Args:
        state: Current graph state containing the running summary and sources gathered

    Returns:
        Dictionary with state update, including running_summary key containing the formatted final summary with sources
    """
    configurable = Configuration.from_runnable_config(config)
    reasoning_model = state.get("reasoning_model") or configurable.answer_model

    # Format the final prompt
    current_date = get_current_date()
    formatted_prompt = answer_instructions.format(
        current_date=current_date,
        research_topic=state["research_topic"],
        summaries="\n\n---\n\n".join(state["web_research_result"]),
    )

    # init Reasoning Model, default to local OpenAI
    llm = ChatOpenAI(
        model=reasoning_model,
        temperature=0,
        max_retries=2,
        base_url="http://localhost:6000/v1",
        api_key="not-needed",
    )
    result = llm.invoke(formatted_prompt)

    # Persist conversation and sources to a log file for post-hoc analysis
    try:
        with open("conversation_logs.txt", "a", encoding="utf-8") as f:
            f.write("\n==== Conversation %s ====\n" % datetime.utcnow().isoformat())
            for msg in state["messages"]:
                f.write(f"USER: {msg.content}\n")
            f.write("--- Answer ---\n")
            f.write(result.content + "\n")
            if state.get("sources_gathered"):
                f.write("--- Sources ---\n")
                for s in state["sources_gathered"]:
                    f.write(str(s) + "\n")
    except Exception as e:
        logger.error("Failed to write conversation log: %s", e)

    return {
        "messages": [AIMessage(content=result.content)],
        "sources_gathered": state.get("sources_gathered", []),
    }


# Create our Agent Graph
builder = StateGraph(OverallState, config_schema=Configuration)

# Define the nodes we will cycle between
builder.add_node("generate_query", generate_query)
builder.add_node("web_research", web_research)
builder.add_node("reflection", reflection)
builder.add_node("finalize_answer", finalize_answer)

# Set the entrypoint as `generate_query`
# This means that this node is the first one called
builder.add_edge(START, "generate_query")
# Add conditional edge to continue with search queries in a parallel branch
builder.add_conditional_edges(
    "generate_query", continue_to_web_research, ["web_research"]
)
# Reflect on the web research
builder.add_edge("web_research", "reflection")
# Evaluate the research
builder.add_conditional_edges(
    "reflection", evaluate_research, ["web_research", "finalize_answer"]
)
# Finalize the answer
builder.add_edge("finalize_answer", END)

graph = builder.compile(name="pro-search-agent")
