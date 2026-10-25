# Bifrost non-delete action policy

Run the service with:

```sh
WORKSPACE_MCP_ACTION_POLICY=organize-no-delete python main.py \
  --transport streamable-http --single-user \
  --permissions gmail:organize drive:full calendar:full --tool-tier complete
```

This deployment policy permits Gmail organization and the supported Drive and Calendar writes. It blocks API delete, batch-delete, trash, empty-trash and calendar-clear requests. It also blocks Gmail Trash/Spam label changes, Drive `trashed=true`, and event `status=cancelled`. Gmail write requests are restricted to message/thread label changes and label creation or updates. Send, draft, filter and settings writes are blocked.

The Google OAuth grant is broader than the exposed MCP actions. These limits apply inside the MCP service. They do not change the permissions of the underlying Google token. Keep the token on the server.

Request construction enforces the policy before requests reach Google. Direct Drive resumable-upload initiation uses the same guard. Bifrost must also use an explicit tool allowlist. Do not expose authentication tools through the gateway.

The policy also blocks delete operations on labels and sharing permissions. A tool that combines allowed updates with a delete can report a partial change. Make such requests separately.

Run the boundary tests without sending Google requests:

```sh
python -m unittest tests.auth.test_bifrost_action_policy
```

The policy is opt-in. An invalid non-empty policy value fails closed. Unset the variable to retain upstream behavior. This does not change any gateway authentication or route rules.
