"""User-visible file generation flows for the bilingual configuration wizard."""

import json
from pathlib import Path

from typer.testing import CliRunner

from powercontext.cli.config import app
from powercontext.cli.env_file import parse_environment


def test_base_wizard_writes_private_environment_without_models(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    database = tmp_path / "context.db"
    # Storage, path, local scene, base capability, no dashboard, no Agent, save.
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{database}\nlocal\nbase\nn\nnone\ny\n",
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_DATABASE_KIND"] == "sqlite"
    assert values["POWERCONTEXT_SERVER_DASHBOARD_ENABLED"] == "false"
    assert "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL" not in values
    assert "API key" not in result.output
    assert "Model service" not in result.output
    assert not database.exists()
    assert output.stat().st_mode & 0o777 == 0o600
    assert CliRunner().invoke(app, ["validate", "--env-file", str(output)]).exit_code == 0


def test_cancel_in_chinese_does_not_write_files(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "zh", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\nn\nnone\nn\n",
    )
    assert result.exit_code == 0, result.output
    assert "配置向导" in result.output
    assert "未写入" in result.output
    assert not list(tmp_path.iterdir())


def test_existing_server_skips_server_models_and_storage(tmp_path: Path) -> None:
    output = tmp_path / "client.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input="remote\nhttps://memory.example.com\nsecret-bearer\nnone\ny\n",
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_CLIENT_SERVER_URL"] == "https://memory.example.com"
    assert values["POWERCONTEXT_CLIENT_API_TOKEN"] == "secret-bearer"  # noqa: S105
    assert not any(key.startswith("POWERCONTEXT_SERVER_") for key in values)
    assert "secret-bearer" not in result.output
    assert "Generation" not in result.output


def test_existing_environment_can_be_reused_without_retyping_models(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    original = "# Keep my comments\nPOWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nCUSTOM_FLAG=untouched\n"
    output.write_text(original)
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nreuse\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "CUSTOM_FLAG=untouched" in output.read_text()
    assert "# Keep my comments" in output.read_text()
    assert "Model service" not in result.output
    assert len(list(tmp_path.glob("server.env.bak-*"))) == 1


def test_eof_never_falls_back_to_a_template(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(app, ["init", "--language", "en", "--output", str(output)], input="")
    assert result.exit_code != 0
    assert not output.exists()


def test_dashboard_and_agent_files_hide_tokens_and_clear_old_bindings(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    client = tmp_path / "server.env.client.env"
    client.write_text(
        "# Keep custom settings\nCUSTOM_FLAG=keep\n"
        "POWERCONTEXT_CODEX_SCOPE_ID=old-scope\n"
        "POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY='{}'\n"
    )
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\ny\ncodex\ny\ndefault\ny\nclaude-code\ny\ndefault\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    server_values = parse_environment(output.read_text())
    client_values = parse_environment(client.read_text())
    token = server_values["POWERCONTEXT_SERVER_AUTH_TOKEN"]
    assert token not in result.output
    assert token not in output.with_name("server.env.next-steps.md").read_text()
    assert server_values["POWERCONTEXT_SERVER_ACCESS_MODE"] == "enforced"
    assert client_values["POWERCONTEXT_CODEX_AUTHORIZATION"] == f"Bearer {token}"
    assert client_values["POWERCONTEXT_CLAUDE_AUTHORIZATION"] == f"Bearer {token}"
    assert "POWERCONTEXT_CODEX_SCOPE_ID" not in client_values
    assert "POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY" not in client_values
    assert client_values["CUSTOM_FLAG"] == "keep"
    assert not any(key.startswith("POWERCONTEXT_SERVER_") for key in client_values)
    assert client.stat().st_mode & 0o777 == 0o600


def test_full_memory_configuration_shares_provider_and_adds_profile_recall(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\nfull\nn\n"
            "bailian\n\n\nexample-test-key\ny\n\nrecommended\ncodex\ny\nexisting\nproject:demo\nn\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    inference = "POWERCONTEXT_SERVER_INFERENCE_"
    assert values[inference + "GENERATION_MODEL"] == "openai-chat:qwen-plus"
    assert values[inference + "EMBEDDING_MODEL"] == "openai:text-embedding-v4"
    assert values[inference + "GENERATION_HEADERS"] == values[inference + "EMBEDDING_HEADERS"]
    assert "example-test-key" not in result.output
    assert CliRunner().invoke(app, ["validate", "--env-file", str(output)]).exit_code == 0
    client = parse_environment(output.with_name("server.env.client.env").read_text())
    assembly = json.loads(client["POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY"])
    assert {section["family"] for section in assembly["sections"]} == {
        "memory",
        "topic-memory",
        "profile",
        "experience",
    }
    assert not any(key.startswith(inference) for key in client)
    assert "profile-policy" in output.with_name("server.env.next-steps.md").read_text()
    assert not (tmp_path / "context.db").exists()


def test_custom_topic_memory_does_not_require_embedding(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\ncustom\n"
            "n\ny\nn\nn\nn\nn\nn\nn\nbailian\n\n\nexample-test-key\nrecommended\nnone\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS"] == "60"
    assert "POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS" not in values
    assert "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL" not in values
    assert "Embedding connection" not in result.output


def test_client_only_output_cannot_reuse_server_secrets_file(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    original = "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nOPENAI_API_KEY=example-test-key\n"
    output.write_text(original)
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input="remote\nhttps://memory.example.com\nexample-token\nnone\ny\n",
    )
    assert result.exit_code != 0
    assert output.read_text() == original
    assert not output.with_name("server.env.next-steps.md").exists()


def test_ssh_forwarding_configures_the_agent_on_the_other_computer(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(f"sqlite\n{tmp_path / 'context.db'}\nremote\nbase\ny\nssh\nt1\n18000\ncodex\nother\n\ny\nnew\nn\ny\n"),
    )
    assert result.exit_code == 0, result.output
    values = parse_environment(output.read_text())
    assert values["POWERCONTEXT_SERVER_HTTP_PORT"] == "8000"
    client = parse_environment(output.with_name("server.env.client.env").read_text())
    assert client["POWERCONTEXT_CODEX_SERVER_URL"] == "http://127.0.0.1:18000"
    steps = output.with_name("server.env.next-steps.md").read_text()
    assert '"url": "http://127.0.0.1:18000/mcp"' in steps
    assert "ssh -N -L 18000:127.0.0.1:8000 t1" in steps


def test_topic_rejects_unsupported_existing_request_settings_without_leaking_values(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    original = (
        "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=test\n"
        'POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS=\'{"extra_body":{"secret":"example-test-key"}}\'\n'
        "POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS=60\n"
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES='[\"topic-memory\"]'\n"
    )
    output.write_text(original)
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nreuse\ny\n",
    )
    assert result.exit_code != 0
    assert "incompatible" in result.output
    assert "example-test-key" not in result.output
    assert output.read_text() == original
    assert not output.with_name("server.env.next-steps.md").exists()


def test_generation_only_existing_configuration_can_edit_model_connection(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    output.write_text("POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=test\n")
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=f"sqlite\n{tmp_path / 'context.db'}\nedit\nmodels\ny\ndone\ny\n",
    )
    assert result.exit_code == 0, result.output
    assert "Keep the existing Generation configuration?" in result.output


def test_processing_choice_says_selected_automatic_capabilities_are_already_enabled(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "zh", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\ncustom\n"
            "n\ny\nn\nn\nn\nn\nn\nn\nbailian\n\n\nkey\nrecommended\nnone\nn\n"
        ),
    )
    assert result.exit_code == 0, result.output
    assert "已启用自动处理" in result.output
    assert "使用推荐周期" in result.output
    assert "逐项自定义周期" in result.output
    assert "关闭后台处理" not in result.output


def test_agents_are_selected_one_at_a_time_and_get_independent_scope_plans(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\nn\ncodex\ny\nnew\ny\nclaude-code\ny\nnew\ny\n"),
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Select an Agent to configure") == 2
    client = parse_environment(output.with_name("server.env.client.env").read_text())
    assert "POWERCONTEXT_CODEX_SCOPE_ID" not in client
    assert "POWERCONTEXT_CLAUDE_SCOPE_ID" not in client
    steps = output.with_name("server.env.next-steps.md").read_text()
    assert '"title": "codex-' in steps
    assert '"title": "claude-code-' in steps
    assert "POST /v1/scopes" in steps
    assert "use the returned scope_id" in steps


def test_existing_scope_is_requested_separately_for_each_agent(tmp_path: Path) -> None:
    output = tmp_path / "server.env"
    result = CliRunner().invoke(
        app,
        ["init", "--language", "en", "--output", str(output)],
        input=(
            f"sqlite\n{tmp_path / 'context.db'}\nlocal\nbase\nn\n"
            "codex\ny\nexisting\nSCOPE_CODEX\ny\nclaude-code\ny\nexisting\nSCOPE_CLAUDE\ny\n"
        ),
    )
    assert result.exit_code == 0, result.output
    client = parse_environment(output.with_name("server.env.client.env").read_text())
    assert client["POWERCONTEXT_CODEX_SCOPE_ID"] == "SCOPE_CODEX"
    assert client["POWERCONTEXT_CLAUDE_SCOPE_ID"] == "SCOPE_CLAUDE"
