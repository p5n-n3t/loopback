# Dashboard

Loopback keeps the CLI as the automation and recovery interface and adds a web
dashboard for operations that benefit from visibility.

Open the dashboard URL with:

```bash
loopback dashboard
```

The dashboard is normally available at:

```text
https://<public-host>/admin
```

or, in local-only mode:

```text
http://127.0.0.1:2026/admin
```

## Authentication

The login page accepts the local Loopback machine token and exchanges it for a
12-hour HttpOnly, SameSite=Strict dashboard session cookie. The machine token
is not stored in browser local storage.

## Current controls

The dashboard shows:

- hostname and Loopback uptime
- system load, memory, disk, and Python version
- current policy profile
- allowed/denied filesystem roots and tool lists
- optional browser-adapter availability/state
- optional native-desktop availability/state
- active background jobs
- active persistent terminals
- pending Loopback approval requests
- 24-hour MCP activity/failure counts
- registered fleet nodes
- recent MCP audit activity

It can:

- switch between standard, trusted, read-only, and locked profiles
- edit allowed/denied roots and allowed/denied MCP tools
- explicitly enable or disable optional browser automation
- explicitly enable or disable native desktop automation
- approve a sensitive Loopback action once or for a bounded number of uses/time
- stop Loopback background jobs
- close Loopback PTY sessions
- add/remove direct fleet nodes

## Why both dashboard and CLI?

The dashboard is for situational awareness and policy. The CLI remains usable
when the browser is unavailable, the public ingress is down, or the server
needs repair.

A good rule is:

- use the dashboard for **observe, approve, configure, inspect**
- use the CLI for **bootstrap, recover, automate, script**

An embedded browser terminal is deliberately not enabled by default. MCP agents
already have PTY tools; exposing another interactive terminal surface in the
admin UI would increase attack surface without being necessary for normal
operation.
