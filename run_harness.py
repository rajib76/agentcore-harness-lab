#!/usr/bin/env python3
"""Invoke a deployed AgentCore harness and stream the response.

Demonstrates the harness's central pattern: defaults live on the resource,
overrides are passed per call. The model, system prompt and tools below apply
to this invocation only — the deployed harness is left untouched.

Usage:
    export HARNESS_ARN=arn:aws:bedrock-agentcore:us-west-2:...:harness/...
    python run_harness.py "Summarize this paper as a bullet list."

If HARNESS_ARN is unset the harness is looked up by name (HARNESS_NAME,
default "research_agent"). Reuse SESSION_ID across runs to continue a
conversation in the same microVM.
"""
import os
import sys
import uuid

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", "us-west-2")
HARNESS_NAME = os.environ.get("HARNESS_NAME", "research_agent")

# The service rejects session ids shorter than 33 characters; a UUID is 36.
SESSION_ID = os.environ.get("SESSION_ID") or str(uuid.uuid4())

MODEL_ID = os.environ.get(
    "MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)


def resolve_harness_arn():
    """Prefer an explicit ARN; otherwise look one up by name.

    ListHarnesses returns the ARN under `arn`, not `harnessArn`. Names are
    prefixed with the project, so a harness declared as `research_agent` in
    project `researchagent` is listed as `researchagent_research_agent` —
    hence the suffix match.
    """
    arn = os.environ.get("HARNESS_ARN")
    if arn:
        return arn

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    found = []
    for page in control.get_paginator("list_harnesses").paginate():
        for h in page.get("harnesses", []):
            name = h.get("harnessName", "")
            if name == HARNESS_NAME or name.endswith(f"_{HARNESS_NAME}"):
                found.append(h)

    if not found:
        sys.exit(
            f"No harness matching {HARNESS_NAME!r} in {REGION}. "
            f"Set HARNESS_ARN, or deploy one with `agentcore deploy`."
        )
    if len(found) > 1:
        names = ", ".join(h["harnessName"] for h in found)
        sys.exit(f"{HARNESS_NAME!r} is ambiguous ({names}). Set HARNESS_ARN.")

    ready = found[0].get("status")
    if ready != "READY":
        print(f"warning: harness status is {ready}", file=sys.stderr)
    return found[0]["arn"]


def run_harness(client, harness_arn, prompt):
    """Invoke the harness and render the event stream. Returns the stop reason.

    InvokeHarness returns events rather than a completed body, so text has to
    be assembled from contentBlockDelta fragments as they arrive.
    """
    response = client.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=SESSION_ID,
        # These apply only to this call; the harness defaults stay intact.
        model={"bedrockModelConfig": {"modelId": MODEL_ID}},
        systemPrompt=[
            {"text": "You are a terse research assistant. One paragraph answers only."}
        ],
        tools=[
            {"type": "agentcore_browser", "name": "browser"},
            {"type": "agentcore_code_interpreter", "name": "code_interpreter"},
        ],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
    )

    stop_reason = None
    for event in response["stream"]:
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start", {})
            if "toolUse" in start:
                print(f"\n  [tool: {start['toolUse']['name']}]", flush=True)

        elif "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta", {})
            if "text" in delta:
                print(delta["text"], end="", flush=True)
            elif "reasoningContent" in delta:
                # Thinking tokens; shown dimmed so they read as distinct.
                text = delta["reasoningContent"].get("text")
                if text:
                    print(f"\033[2m{text}\033[0m", end="", flush=True)

        elif "messageStop" in event:
            stop_reason = event["messageStop"].get("stopReason")
            print(f"\n\n--- stopReason: {stop_reason}")

        elif "metadata" in event:
            usage = event["metadata"].get("usage", {})
            metrics = event["metadata"].get("metrics", {})
            print(
                f"--- tokens in/out: {usage.get('inputTokens')}/"
                f"{usage.get('outputTokens')}"
                f"  latency: {metrics.get('latencyMs')} ms"
            )

        # Three distinct failure events; none of them raise.
        elif "runtimeClientError" in event:
            print(f"\nRuntime error: {event['runtimeClientError']['message']}",
                  file=sys.stderr)
        elif "validationException" in event:
            err = event["validationException"]
            print(f"\nValidation error: {err.get('message')}", file=sys.stderr)
            for f in err.get("fieldList") or []:
                print(f"  {f.get('name')}: {f.get('message')}", file=sys.stderr)
        elif "internalServerException" in event:
            print(f"\nServer error: {event['internalServerException']}",
                  file=sys.stderr)

    return stop_reason


def main():
    prompt = " ".join(sys.argv[1:]) or "Summarize this paper as a bullet list."
    client = boto3.client("bedrock-agentcore", region_name=REGION)
    harness_arn = resolve_harness_arn()

    print(f"harness: {harness_arn}")
    print(f"session: {SESSION_ID}  (reuse via SESSION_ID to continue)\n")

    try:
        stop_reason = run_harness(client, harness_arn, prompt)
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "AccessDeniedException" and "marketplace" in str(e).lower():
            sys.exit(
                f"\n{e}\n\n"
                f"This usually means the account holds no Marketplace agreement "
                f"for {MODEL_ID}, not that IAM is misconfigured. See Lab 01 Step 5."
            )
        raise

    # Limits you set yourself surface here rather than as exceptions.
    if stop_reason in {"max_iterations_exceeded", "timeout_exceeded",
                       "max_output_tokens_exceeded"}:
        sys.exit(f"Stopped early: {stop_reason}")


if __name__ == "__main__":
    main()
