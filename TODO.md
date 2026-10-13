# TODO

Status on 2026-10-13: Phases 1–12 are done and committed. The Streamlit demo works locally (replay + approve/reject).
Left: publishing, hosted-model providers, and a hosted-model eval row.

## 1. Publish (needs you first)
- [ ] **You:** create an empty public GitHub repo (no README / licence / .gitignore) and send the URL.
- [ ] **Me:** push the repo; check the GitHub Actions CI run is green.
- [ ] **You OK → me:** upload the 193 MB test-split DB to the public HF dataset `dheepakkaran/incident-triage-telemetry`
      (`python demo/build.py --results eval/results/2026-10-13_qwen3-8b_test_v3_reviewer-on_agent.json --upload`).
- [ ] **You:** deploy on share.streamlit.io: repo, branch `main`, file `demo/streamlit_app.py`, Python 3.12, secret `GROQ_API_KEY`.
- [ ] **Me:** measure cold start and per-run time on Streamlit Cloud; add the demo link and those numbers to the README.

## 2. LLM providers and keys
- [x] **Me (2026-10-13):** separate APIs per agent, done and tested on real keys (case_001 end to end: 1 min 6 s,
      correct; Gemma review ~4 s with minimal thinking; Groq's 8K tokens/min limit is the main wait): `INVESTIGATOR_PROVIDER_CHAIN` (Scout: Groq `openai/gpt-oss-120b`) and
      `REVIEWER_PROVIDER_CHAIN` (Skeptic: Google AI Studio `gemma-4-31b-it`, a different model family for an independent
      check); both fall back to Ollama and default to `LLM_PROVIDER_CHAIN`. Two LLM clients in `Deps`; record each role's
      model in traces, reports and eval results; show each bot's model in the demo boxes.
      Measured split per case: Scout's side ~8.9K tokens, Skeptic's side ~4.6K (test runs, reviewer on vs off).
- [ ] **You:** if not done yet, revoke the two keys pasted in chat on 2026-10-13 and put fresh ones in `.env` yourself
      (`GROQ_API_KEY=` line 12, `GOOGLE_API_KEY=` line 13; the Google one comes from aistudio.google.com/apikey, starts with `AIza`).
      Then I run `python -m triage smoke llm` (it tests Scout's and Skeptic's chains separately).
- [ ] **You:** other free keys, same way, never in chat:
      Groq, Google AI Studio (Gemma 4), Cerebras (30-day trial), Mistral; OpenRouter optional.
- [ ] **Me:** add providers `cerebras`, `mistral` (OpenAI-compatible endpoints) and `google` (Gemma 4 via `langchain-google-genai`);
      default chain `groq,cerebras,google,mistral,ollama`; smoke-test tool calling on each (`python -m triage smoke llm`).
- [x] **Me:** lower the demo's daily live cap from 50 to 12 (Groq free tier ≈ 11–14 cases/day with the reviewer on).
- [ ] **Me:** add `DEMO_LIVE_PROVIDER` so live mode can use local Ollama when the demo runs on your laptop.
- [ ] **Me:** test live mode end to end on Groq (approve and reject), and check the live flow/typing stays in sync.

## 3. Eval
- [ ] **Me:** hosted-model results row: test split (15 cases) on `gpt-oss-120b`, ideally all 40 runs (dev + test, reviewer on/off)
      on Cerebras in one day; add to README next to the qwen3:8b rows, whatever the result.
- [ ] Idea, not done: when the fault-type evidence guardrail fails, keep the service and fall back to `fault_type: unknown`
      (3 of 5 test misses were right-service escalations). Needs new cases to evaluate fairly: the test split is used.

## 4. Small things
- [ ] **You:** run the MCP Inspector check (needs Node): `npx @modelcontextprotocol/inspector .venv/bin/python -m triage mcp`.
- [ ] **You:** unpause the `multi-camera-bev` Space on Hugging Face (paused during the hosting attempt).
