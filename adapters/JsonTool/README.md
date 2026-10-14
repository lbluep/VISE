# JsonTool

jq-lite in pure Python stdlib — query, pretty-print, validate and inspect
JSON without installing jq. Pipe-friendly: pass `-` as the file to read stdin.

## Usage

```bash
visectl run json_tool get .vise/registry.json "adapters.0.adapter_id"
visectl run json_tool keys .vise/registry.json
visectl run json_tool validate vise.adapter.json
visectl run json_tool pretty data.json
```
