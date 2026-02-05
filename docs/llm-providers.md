# LLM Providers Through LiteLLM Proxy

## Local model hosted on domain

The LiteLLM proxy can forward requests to any OpenAI-compatible servers. [vLLM](https://docs.vllm.ai/en/stable/serving/openai_compatible_server/) in particular is verified by us to be compatible. 

In OSS-CRS, you will want to set a custom LiteLLM proxy configuration.

As documented in [LiteLLM Providers](https://docs.litellm.ai/docs/providers/openai_compatible#usage-with-litellm-proxy-server), the important part is to prefix your available model names with `openai/` for `model_list[].litellm_params.model`. However, you can still use your original model name or alias them with `model_list[].model_name`.

```yaml example_configs/test-local/config-litellm.yaml
model_list:
- model_name: "claude-opus-4-5-20251101"    # alias model name to the ones CRS uses
  litellm_params:
    model: "openai/Qwen/Qwen3-0.6B"         # openai/{MODEL_NAME}
    api_key: os.environ/VLLM_KEY            # set in local model server
    api_base: https://example.com/v1        # known domain
```

The environment that was tested looks like the following.

```
+-------------+                    +---------------+
| Local Model | -- HTTP :8000 -->  | Reverse Proxy |
+-------------+                    +---------------+
                                           ^
                                           |
                                        HTTPS /v1
                                           |
                                       +---------+
                                       | OSS-CRS |
                                       +---------+
```

## Local model hosted on local machine

We also tested OSS-CRS in a completely local setting where CRSs are run on the same machine models are hosted on (e.g. desktops). You can set the host as the Docker interface IP. Make sure expose the LLM server's port on the Docker interface in your firewall.

```yaml example_configs/test-local/config-litellm.yaml
model_list:
- model_name: "claude-opus-4-5-20251101"    # alias model name to the ones CRS uses
  litellm_params:
    model: "openai/Qwen/Qwen3-0.6B"         # openai/{MODEL_NAME}
    api_key: os.environ/VLLM_KEY            # set in local model server
    api_base: http://172.17.0.1:8000/v1     # IP retrieved from `ip addr show docker0`
```

The environment that was tested looks like the following.

```
+-------------+
| Local Model |
|     ^       |
|     |       |
|  HTTP /v1   |
|     |       |
|  OSS-CRS    |
+-------------+
```

# Verifying LiteLLM Proxy

We added a CRS called `test-local` to check the LiteLLM proxy forwarding.

You'll need to first update `example_configs/test-local/config-litellm.yaml` with your key, model names, and endpoint URL.

```sh
# Set LLM key (can rename environment variable, see NOTE in config-litellm.yaml)
export VLLM_KEY=<SECRET_KEY>

# No-op, needed to pass runtime checks
uv run oss-bugfind-crs build example_configs/test-local json-c 

# Should say hello from LLM
uv run oss-bugfind-crs run example_configs/test-local json-c json_array_fuzzer
```

# Known Issues with Aliasing Models

Recent LLMs like GPT-5 no longer support the `temperature` parameter. This can cause silent failures if you’re swapping model backends in LiteLLM while keeping the original model name (e.g., model name set to gpt-4o but relay configured to gpt-5). The temperature param gets passed through and the API rejects it.
