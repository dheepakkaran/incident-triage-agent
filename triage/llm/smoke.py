"""`python -m triage smoke llm`: one tool-calling request through the provider chain, sent twice.

The second call has the same prompt, so it must come from the disk cache.
"""

from langchain_core.messages import HumanMessage, SystemMessage
from rich.console import Console

from triage.llm.providers import LLMClient
from triage.tools import tool_schemas


def smoke_llm() -> None:
    console = Console()
    client = LLMClient.from_settings()
    console.print("Providers available: " + ", ".join(f"{h.provider}:{h.model}" for h in client.handles))
    messages = [
        SystemMessage("You are testing tool calling. Call the tool you are asked to call."),
        HumanMessage("Call get_metric_changes for the service 'frontend' with top_n 5."),
    ]
    tools = tool_schemas(["list_services", "get_metric_changes"])
    for attempt in (1, 2):
        reply, record = client.invoke(messages, tools=tools, purpose="smoke")
        console.print(
            f"call {attempt}: provider={record['provider']} model={record['model']} "
            f"latency={record['latency_s']}s tokens in/out={record['input_tokens']}/{record['output_tokens']} "
            f"cached={record['cached']}"
        )
        console.print(f"  tool_calls={reply.tool_calls}")
