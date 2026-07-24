# Vendored static assets

These files are served by the web console at `/ui/static/<name>` (see
`agent_mailbox_old.webui`). They are vendored (not fetched from a CDN) so the console is
fully self-contained and works on an offline LAN.

## vis-network.min.js

- **Library:** [vis-network](https://visjs.github.io/vis-network/) (visjs)
- **Version:** 10.1.0
- **License:** dual Apache-2.0 / MIT (the license header is preserved in the file)
- **Used by:** the `/ui/flow` message-flow graph

To update: download the standalone UMD build and replace the file, keeping the
version note here in sync:

```
curl -sL https://cdn.jsdelivr.net/npm/vis-network@<version>/standalone/umd/vis-network.min.js \
  -o src/agent_mailbox_old/static/vis-network.min.js
```
