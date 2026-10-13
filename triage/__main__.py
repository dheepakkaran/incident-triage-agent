"""Command line entry point: `python -m triage <command>`.

Imports happen inside each handler so `--help` stays fast and a command only loads
what it needs (for example, `data prepare` never loads the LLM libraries).
"""

import argparse
import sys


def cmd_data(args: argparse.Namespace) -> int:
    if args.action == "download":
        from triage.data.download import download

        download()
    else:
        from triage.data.prepare import prepare

        prepare()
    return 0


def cmd_runbooks(args: argparse.Namespace) -> int:
    from triage.tools.runbooks import check_retrieval, index_runbooks

    if args.action == "index":
        index_runbooks(rebuild=True)
    else:
        check_retrieval()
    return 0


def cmd_smoke(args: argparse.Namespace) -> int:
    if args.target == "tools":
        from triage.tools.smoke import smoke_tools

        smoke_tools(args.case)
    elif args.target == "mcp":
        from triage.mcp_server import smoke_mcp

        smoke_mcp()
    else:
        from triage.llm.smoke import smoke_llm

        smoke_llm()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from triage.cli_run import run_case_cli

    return run_case_cli(
        case_id=args.case,
        auto_approve=args.auto_approve,
        reviewer=args.reviewer,
        provider=args.provider,
    )


def cmd_eval(args: argparse.Namespace) -> int:
    if args.suite == "adversarial":
        from eval.adversarial import run_adversarial

        run_adversarial(provider=args.provider)
    elif args.suite == "baseline":
        from eval.baseline import run_baseline

        run_baseline(split=args.split)
    else:
        from eval.run_eval import run_eval

        run_eval(split=args.split, reviewer=args.reviewer, provider=args.provider)
    return 0


def cmd_mcp(args: argparse.Namespace) -> int:
    from triage.mcp_server import main

    main()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m triage", description="Incident triage agent (RCAEval RE2).")
    sub = parser.add_subparsers(dest="command", required=True)

    data = sub.add_parser("data", help="download or prepare the RCAEval RE2 subset")
    data.add_argument("action", choices=["download", "prepare"])
    data.set_defaults(func=cmd_data)

    runbooks = sub.add_parser("runbooks", help="index runbooks into Chroma or check retrieval")
    runbooks.add_argument("action", choices=["index", "check"])
    runbooks.set_defaults(func=cmd_runbooks)

    smoke = sub.add_parser("smoke", help="quick checks: all tools on a case, one LLM call, or the MCP server")
    smoke.add_argument("target", choices=["tools", "llm", "mcp"])
    smoke.add_argument("--case", default="case_001")
    smoke.set_defaults(func=cmd_smoke)

    run = sub.add_parser("run", help="triage one case end to end")
    run.add_argument("--case", required=True, help="case id, e.g. case_001")
    run.add_argument("--auto-approve", action="store_true", help="approve risky actions without asking")
    run.add_argument("--reviewer", choices=["on", "off"], default=None, help="override REVIEWER_ENABLED")
    run.add_argument("--provider", default=None, help="use only this provider, e.g. groq or ollama")
    run.set_defaults(func=cmd_run)

    ev = sub.add_parser("eval", help="run the main eval, the baseline, or the adversarial suite")
    ev.add_argument("suite", nargs="?", choices=["main", "baseline", "adversarial"], default="main")
    ev.add_argument("--split", choices=["dev", "test"], default="dev")
    ev.add_argument("--reviewer", choices=["on", "off"], default=None, help="override REVIEWER_ENABLED")
    ev.add_argument("--provider", default=None, help="use only this provider for the whole eval")
    ev.set_defaults(func=cmd_eval)

    mcp = sub.add_parser("mcp", help="serve the 5 read-only tools over MCP (stdio)")
    mcp.set_defaults(func=cmd_mcp)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
