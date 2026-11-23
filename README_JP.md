<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/readme-hero-en-dark.gif" />
    <img src="docs/assets/readme-hero-en-light.gif" width="100%" alt="PowerContext：SWE-bench Pro の同じ 24 タスクを比較し、両群が同じ 16 タスクを解決。記憶の生成を含むトークン数は 2,641 万から 1,781 万へ、32.57% 減少しました。内訳は問題解決に 1,577 万、記憶の生成に 204 万。問題解決のみでは 40.30% 減少し、記憶の生成を含む推定モデルコストは 15.42% 低下。単一タスクの最大トークン削減率は 63.49%。" />
  </picture>
</p>

<p align="center">
  <a href="#クイックスタート"><b>使い始める</b></a> &nbsp; · &nbsp;
  <a href="#効果の検証">検証結果</a> &nbsp; · &nbsp;
  <a href="#さまざまな場面でコンテキストを活かす">活用シーン</a> &nbsp; · &nbsp;
  <a href="https://powercontext.oceanbase.io/en/docs/">ドキュメント</a> &nbsp; · &nbsp;
  <a href="https://discord.com/invite/74cF8vbNEs">Discord</a>
</p>
<p align="center">
  <a href="README.md">English</a> · <a href="README_CN.md">中文</a> · <a href="README_JP.md">日本語</a> &nbsp; / &nbsp;
  <a href="https://pypi.org/project/powercontext/">Python パッケージ</a> · <a href="LICENSE">Apache 2.0</a>
</p>

本文中のドキュメントリンクと図中の表記は英語版です。

## なぜ PowerContext なのか？

**的確に、素早く、無駄なく。必要なコンテキストで、Agent の遠回りを減らす。** PowerContext は、関連する知識、ユーザーの好み、タスクの進捗、過去の経験を今のタスクに届けます。検索の繰り返し、何度もの確認、成果につながらない試行を減らし、時間とトークンを作業の前進に使えるよう支援します。

- **的確に：今のタスクに必要なコンテキストを届ける。** 関連する知識やコードをタスクに応じて探し、根拠を残し、コードを使う前に更新の有無を確認します。
- **素早く：調べ直しや往復を減らす。** これまでの進捗を引き継ぎ、確認済みの方法や制約を参照して、背景の把握や試行錯誤にかかる時間を減らせるようにします。
- **無駄なく：タスク完了までの総コストを抑える。** 1 回ごとのモデル呼び出しを有効に使い、不要な推論やツールとの往復、ユーザーの待ち時間や背景を説明し直す手間を減らせるようにします。

## PowerContext とは？

**PowerContext は、AI Agent 向けのオープンソース・コンテキストサービスです。** 会話、資料、作業記録を、検索・更新でき、出典をたどれる知識に整理します。Agent がユーザーやプロジェクトを理解し、前回の続きから作業できるようにします。

何を重点的に記憶するか、情報をどう整理するか、プロフィールや経験をどう生成するか。それぞれの処理に使うプロンプトを、自分の Scope でカスタマイズできます。個人のプロフィール、プロジェクトの知識、タスクの引き継ぎと組み合わせることで、蓄積するコンテキストを人や仕事に合わせられます。

HTTP、MCP、Python、Agent 連携を通じてサービスとして利用するほか、Core をアプリケーションに組み込むこともできます。[製品とワークフローを詳しく見る →](https://powercontext.oceanbase.io/en/docs/workflows/memory-and-handoff/)

## 効果の検証

**トークンを減らし、モデルコストも抑える。** SWE-bench Pro の同じ 24 タスクで、PowerContext は記憶の生成を含めてトークン数を **32.57%**、推定モデルコストを **15.42%** 削減しました。両群が解決したのは**同じ 16 タスク**です。

**問題解決のみではトークン数が 40.30% 減少し、記憶の生成に使った分を含めても 32.57% 減少しました。** 保存した記憶は後のセッションで再利用できます。このベンチマークが測ったのは今回の実行であり、後の再利用による効果は測定していません。

**単一タスクで最大 63.49% のトークン削減、51.98% の推定モデルコスト削減。** タスク 456 は両群とも解決し、記憶の生成を含むトークン数は 230 万から 84 万に減少しました。

[24 タスクの検証結果、集計方法、実行設定を見る →](docs/benchmarks/token-savings/README.md)

### コーディング：探索のやり直しと不要な往復を減らす

**1 回ごとの推論を有効に使い、タスク完了までの往復を減らす。** 関連するコード、明確な作業要件、過去の経験は、Agent が次に何をすべきかを見つける手がかりになります。タスク全体にかかる時間とトークンに着目し、探索のやり直し、背景の読み直し、成果につながらない試行を減らすことを目指しています。

このベンチマークでは**ゲートウェイモード**を使い、コード検索、作業指針、プロジェクトの記憶を同時に有効にしています。

<img src="docs/assets/readme-capabilities-en.svg" width="100%" alt="CodeGraph：関連するコードを検索し、更新の有無を確認。Steering：対象を絞った読み込み、操作の一括実行、関連するテスト。Memory：判断を保存し、後のセッションで予算内に収まる範囲で想起。" />

**記憶を抽出し、キャッシュも再利用する。** 上記の 32.57% には記憶の生成に使ったトークンも含まれています。ゲートウェイモードでは、プレフィックスキャッシュを使った記憶抽出を有効にできます。条件を満たす場合に問題解決リクエストのコンテキストプレフィックスを再利用し、キャッシュにヒットした入力のコストを抑えます。今回、キャッシュ項目が記録された記憶生成リクエストでは、入力の 97% がキャッシュにヒットしました。

### モデルとの往復を減らす

**同じ 24 タスクを使った別のテストでは、主モデルへのリクエスト数が 23.87% 減少しました。** 記憶と作業指針を有効にした構成では、リクエスト総数は **708 回から 539 回**、1 タスクあたりの平均は **29.50 回から 22.46 回**になりました。両群が解決したのは**同じ 15 タスク**です。このテストは、冒頭の図に示したコード検索・作業指針・記憶の組み合わせによる比較とは別に集計しています。[リクエスト数とタスクごとの記録を見る →](docs/benchmarks/token-savings/request-counts_JP.md)

## さまざまな場面でコンテキストを活かす

### 自分の記憶を、自分のルールで

**記憶のルールは、一人ひとりに合わせられます。** エンジニアなら技術的な判断や障害の原因、プロダクトマネージャーなら要件や設計上のトレードオフ、クリエイターなら表現の好みや作品の設定。個人の Scope でプロンプトをカスタマイズし、記憶の抽出、情報の整理、プロフィールの生成で重視する内容を調整できます。設定は Scope ごとに保存され、バージョンも保持されます。

プロフィールは Agent があなたを理解するために使われ、個人用のプロンプトは役立つ記憶をどう蓄積するかを定めます。使うたびに残る情報を、あなたの仕事や習慣に合わせていけます。

[Prompt：記憶のルールをカスタマイズする →](https://powercontext.oceanbase.io/en/docs/workflows/manage-prompts/) · [Profile：一人ひとりのユーザーを理解する →](https://powercontext.oceanbase.io/en/docs/workflows/use-profiles/)

### セッションが変わっても、続きから

**作業が中断しても、最初からやり直す必要はありません。** 調べた原因、確認できたこと、残っている問題、次にやることを残せます。新しいセッションや別の Agent では、まず現在の状態を確かめてから、引き継いだ地点から再開します。

Memory は判断や制約を保持し、Handoff は目標、進捗、次の手順を整理します。数日にまたがるタスク、チームでの作業、複数の Agent 間の引き継ぎに役立ちます。

[Memory と Handoff：進捗を保存し、タスクを引き継ぐ →](https://powercontext.oceanbase.io/en/docs/workflows/memory-and-handoff/)

### 長く続くプロジェクトでも、背景の説明を繰り返さない

**プロジェクトの経緯を、Agent も参照できるように。** なぜこの方法を選んだのか、顧客からどんな要望があったのか、研究で何が分かっているのか。資料や議論に分散した情報を、継続して更新できるテーマ別の知識に整理し、必要なときに背景や根拠を探せます。

Topic Memory は同じテーマの進展をまとめ、Memory は具体的な事実を保持し、Source は元の根拠を残します。製品開発、顧客プロジェクト、長期的な研究に役立ちます。

[Topic Memory：プロジェクトの知識を継続して整理する →](https://powercontext.oceanbase.io/en/docs/workflows/topic-memory/) · [Source：元の根拠を確認する →](https://powercontext.oceanbase.io/en/docs/workflows/sources/)

### 一度解いた問題を、次の仕事の手がかりに

**問題を解決した経験を、次の出発点に。** 有効だった方法、適用できる条件、失敗からの教訓、検証手順を残します。似たタスクに取り組むとき、Agent は既存の経験を調べてから、どう進めるかを判断できます。

Experience は状況、方法、結果を蓄積します。繰り返し使える手順は Skill にまとめ、確認してからエクスポートして Agent で使えます。一度取り組んだ仕事の成果を、その後の仕事にも活かせます。

[Experience：再利用できる経験を蓄積する →](https://powercontext.oceanbase.io/en/docs/workflows/experience-and-skill-lifecycle/) · [Skill：確認済みの作業手順をエクスポートする →](https://powercontext.oceanbase.io/en/docs/workflows/create-and-export-skill/)

個人、プロジェクト、チームごとの Scope でコンテキストを管理し、アクセス制御と組み合わせて、誰が読み取りや変更をできるかを決められます。内容のバージョンと出典を保持するため、確認、修正、引き継ぎにも役立ちます。[Scope とアクセス制御を詳しく見る →](https://powercontext.oceanbase.io/en/docs/workflows/scopes-and-access/)

## クイックスタート

Python 3.11+、Git、[uv](https://docs.astral.sh/uv/getting-started/installation/) と、接続する Agent の CLI が必要です。以下は macOS と Linux 向けにバージョン **1.5.2** をインストールする手順です。Windows のサポートは実験段階です。[インストールスクリプト](https://powercontext.oceanbase.io/en/docs/get-started/install-and-run/#install-with-the-recommended-script)を使えば、Python と uv も用意できます。

ウィザードは英語で表示されます。Server 用の追加のモデル API なしで明示的な保存と想起を試すには、**Basic memory** を選んでください。自動抽出には、Generation と Embedding の認証情報を設定した **Full memory** が必要です。最初のストレージには SQLite が手軽です。

```bash
uv tool install --force "powercontext[cli,server]==1.5.2"
mkdir -p powercontext-config && cd powercontext-config
powercontext config init --language en --output .env
powercontext config validate --env-file .env
powercontext service install --env-file .env
powercontext service status
```

サービスはターミナルを閉じた後も動作し続けます。Linux では `systemd --user` が利用できる必要があります。生成される `.env` と `.env.next-steps.md` には認証情報が含まれることがあるため、Git にコミットしないでください。

生成された環境ファイルを読み込み、サービスを確認します。

```bash
set -a
. ./.env
set +a
powercontext ready
powercontext capabilities
```

**`.env.next-steps.md`** に従って Scope を作成し、作成リクエストが返した実際の `scope_id` を使って紐付けます。Dashboard と Agent は同じ Server と Scope に接続してください。ディレクトリを変えるだけではプロジェクトは分離されません。`.env` を編集した場合は再度読み込み、`powercontext service install --env-file .env` を再実行して反映します。

Codex には対応するバージョンの連携をインストールします。

```bash
powercontext setup codex --env-file .env --ref powercontext-v1.5.2
powercontext doctor codex
```

新しい Codex セッションで最初のメッセージを送る前に、**`/hooks`** を開き、求められたら PowerContext Hook の信頼を許可してください。入力の自動取得と想起には `UserPromptSubmit` Hook の有効化が必要です。MCP に接続できるだけでは動作しません。[初回確認と復旧 →](https://powercontext.oceanbase.io/en/docs/integrations/codex/#first-session-hook-trust)

選んだワークフローを確認します。

- **Basic memory：** Agent にプロジェクトの判断を明示的に保存させ、同じ Scope の新しいセッションで、出典を参照しながら想起できることを確認します。
- **Full memory：** 入力が Source として保存され、設定した処理によって Topic Memory が生成されるのを待ちます。続いて関連する更新を送り、その内容が反映され、新しいセッションで想起できることを確認します。

`doctor codex` が確認するのはインストールと接続だけです。Source の取得だけでは記憶は作られず、自動処理には設定したスケジュールとモデルの実行も必要です。Basic memory でも Agent 自身のモデルは使用します。[完全なセットアップ手順 →](https://powercontext.oceanbase.io/en/docs/get-started/quickstart/)

<sub>この手順はコンテキストサービスを起動するものです。ベンチマークの Gateway、CodeGraph、Steering の設定は自動では再現されません。詳細は検証レポートを参照してください。</sub>

## 今のワークフローに接続する

<!-- Add integrations using an original logo asset, a product name, and a documentation link. -->
<table width="100%">
  <tr>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/codex/"><img src="docs/assets/codex.png" height="48" alt="Codex" /><br />Codex</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/claude-code/"><img src="docs/assets/claude-code.png" height="48" alt="Claude Code" /><br />Claude Code</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/minimax#readme"><img src="docs/assets/minimax-code.png" height="48" alt="MiniMax Code" /><br />MiniMax Code</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/dsh/"><img src="docs/assets/deepseek.png" height="48" alt="DeepSeek Harness" /><br />DeepSeek Harness</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/zcode/"><img src="docs/assets/zcode.png" height="48" alt="ZCode" /><br />ZCode</a></td>
  </tr>
  <tr>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/kimi-code/"><img src="docs/assets/kimi.png" height="48" alt="Kimi Code" /><br />Kimi Code</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/hermes#readme"><img src="docs/assets/hermes.png" height="48" alt="Hermes Agent" /><br />Hermes Agent</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/pi/"><img src="docs/assets/pi.png" height="48" alt="Pi Coding Agent" /><br />Pi Coding Agent</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/openclaw/"><img src="docs/assets/openclaw.png" height="48" alt="OpenClaw" /><br />OpenClaw</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/opencode/"><img src="docs/assets/opencode.png" height="48" alt="OpenCode" /><br />OpenCode</a></td>
  </tr>
  <tr>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/workbuddy#readme"><img src="docs/assets/workbuddy.svg" height="48" alt="WorkBuddy" /><br />WorkBuddy</a></td>
    <td align="center" width="20%"><a href="https://github.com/oceanbase/powercontext/tree/master/integrations/bub#readme"><img src="docs/assets/bub.png" height="48" alt="Bub" /><br />Bub</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/pydantic-ai/"><img src="docs/assets/pydantic.png" height="48" alt="Pydantic AI" /><br />Pydantic AI</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/langchain/"><img src="docs/assets/langchain.png" height="48" alt="LangChain" /><br />LangChain</a></td>
    <td align="center" width="20%"><a href="https://powercontext.oceanbase.io/en/docs/integrations/langgraph/"><img src="docs/assets/langgraph.png" height="48" alt="LangGraph" /><br />LangGraph</a></td>
  </tr>
</table>

Codex 連携は公式メンテナンス、その他の Agent と Python フレームワークの連携はコミュニティメンテナンスです。Bub は評価専用です。対応機能は機能一覧を参照してください。

[その他の連携と機能一覧 →](https://powercontext.oceanbase.io/en/docs/integrations/capabilities/)

**アプリケーションとチーム** — [Python / HTTP / MCP](https://powercontext.oceanbase.io/en/docs/develop/interfaces/) · [Memory と Handoff](https://powercontext.oceanbase.io/en/docs/workflows/memory-and-handoff/) · [デプロイとアクセス制御](https://powercontext.oceanbase.io/en/docs/workflows/scopes-and-access/)

---

[ドキュメント](https://powercontext.oceanbase.io/en/docs/) · [開発に参加](https://github.com/oceanbase/powercontext/blob/master/CONTRIBUTING.md) · [問題を報告](https://github.com/oceanbase/powercontext/issues) · [Discord](https://discord.com/invite/74cF8vbNEs)

PowerContext は [PowerMem](https://www.powermem.ai/) の後継プロジェクトであり、[Apache 2.0](LICENSE) ライセンスで公開されています。
