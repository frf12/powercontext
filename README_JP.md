<p align="center">
    <a href="https://github.com/oceanbase/oceanbase">
        <img alt="OceanBase Logo" src="docs/images/oceanbase_Logo.png" width="50%" />
    </a>
</p>

<p align="center">
  <a href="https://powermem.ai">Learn more</a>
  ·
  <a href="https://discord.com/invite/74cF8vbNEs">Join Discord</a>
  ·
  <a href="https://powermem.ai/benchmark">Benchmark Result</a>
</p>

<p align="center">
    <a href="https://pepy.tech/project/powermem">
        <img src="https://img.shields.io/pypi/dm/powermem" alt="PowerMem PyPI - Downloads">
    </a>
    <a href="https://github.com/oceanbase/powermem">
        <img src="https://img.shields.io/github/commit-activity/m/oceanbase/powermem?style=flat-square" alt="GitHub commit activity">
    </a>
    <a href="https://pypi.org/project/powermem" target="blank">
        <img src="https://img.shields.io/pypi/v/powermem?color=%2334D058&label=pypi%20package" alt="Package version">
    </a>
    <a href="https://github.com/oceanbase/powermem/blob/master/LICENSE">
        <img alt="license" src="https://img.shields.io/badge/license-Apache%202.0-green.svg" />
    </a>
    <a href="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg">
        <img alt="pyversions" src="https://img.shields.io/badge/python%20-3.10.0%2B-blue.svg" />
    </a>
    <a href="https://deepwiki.com/oceanbase/powermem">
        <img alt="Ask DeepWiki" src="https://deepwiki.com/badge.svg" />
    </a>
</p>

[English](README.md) | [中文](README_CN.md) | [日本語](README_JP.md)

## ハイライト

<div align="center">

<img src="docs/images/benchmark_metrics_jp.svg" alt="PowerMem LOCOMO ベンチマーク指標" width="900"/>

</div>

- **より正確**：**[精度 48.77% 向上]** LOCOMO ベンチマークで full-context より正確（78.70% VS 52.9%）
- **より高速**：**[91.83% 高速な応答]** full-context と比較し、検索の p95 遅延が大幅に減少（1.44s VS 17.12s）
- **より経済的**：**[96.53% トークン削減]** full-context と比較し、性能を犠牲にすることなくコストを大幅に削減（0.9k VS 26k）

- [ベンチマーク詳細を参照](https://powermem.ai/benchmark)

# PowerMem - インテリジェントメモリシステム

AI アプリケーション開発において、大規模言語モデルが履歴会話、ユーザー設定、コンテキスト情報を永続的に「記憶」できるようにすることは、核心的な課題です。PowerMem は、ベクトル検索、全文検索、グラフデータベースのハイブリッドストレージアーキテクチャを組み合わせ、認知科学のエビングハウス忘却曲線理論を導入して、AI アプリケーション向けの強力なメモリインフラストラクチャを構築します。システムは、エージェントメモリの分離、エージェント間のコラボレーションと共有、きめ細かい権限制御、プライバシー保護メカニズムを含む、包括的なマルチエージェントサポート機能も提供し、複数の AI エージェントが独立したメモリ空間を維持しながら効率的なコラボレーションを実現できるようにします。

## 核心機能（下記のデモリンク）

### インテリジェントメモリ管理
- **[メモリのインテリジェント抽出](docs/examples/scenario_2_intelligent_memory.md)**：LLM モデルによるメモリの抽出
- **エビングハウス忘却曲線**：認知科学に基づくスマートメモリ最適化
- **メモリ減衰と強化**：使用パターンに基づく動的メモリ保持

### マルチエージェントサポート
- **[エージェント分離](docs/examples/scenario_3_multi_agent.md)**：異なるエージェント用の独立したメモリ空間

### マルチモーダルサポート
- **テキストと画像メモリ**：テキストと画像の2つのモーダルのメモリ保存と検索をサポートし、より豊富なコンテキスト理解を実現

### 深く最適化されたデータストレージ
- **サブストア（Sub Stores）サポート**：サブストアによるデータのパーティション管理で、超大规模データに対応
- **ハイブリッド検索**：ベクトル検索、全文検索、グラフ検索のハイブリッド検索機能をサポート
- **グラフ検索**：LLM によるエンティティと関係の抽出をサポートし、ナレッジグラフを構築。複雑なメモリ関係を検索するためのマルチホップグラフトラバーサル
- **ハイブリッドストレージ**：ベクトル検索とグラフ関係を組み合わせて検索を強化

### 開発者フレンドリー
- **軽量な統合方式**：Python SDK/MCP の統合方式をサポートし、mem0 の使用と互換性あり

## クイックスタート

### インストール

```bash
pip install powermem
```

### 基本的な使用方法

**✨ 最も簡単な方法**：`.env` ファイルから自動的にメモリを作成！[設定ファイル参照](configs/env.example)

```python
from powermem import create_memory

# .env から自動的に読み込む
memory = create_memory()

# メモリを追加
memory.add("ユーザーはコーヒーが好き", user_id="user123")

# メモリを検索
memories = memory.search("ユーザー設定", user_id="user123")
for memory in memories:
    print(f"- {memory.get('memory')}")
```

より詳細な例と使用パターンについては、[はじめにガイド](docs/guides/0001-getting_started.md) を参照してください。

## 統合とデモ

- **LangChain 統合**: LangChain + PowerMem + OceanBase を使用して医療サポートロボットを構築 ([Example](examples/langchain/README.md))
- **Langgraph 統合**: LangGraph + PowerMem を使用してカスタマーロボットを構築 ([Example](examples))

## ドキュメント

- **[はじめに](docs/guides/0001-getting_started.md)**：インストールとクイックスタートガイド
- **[設定ガイド](docs/guides/0002-configuration.md)**：完全な設定オプション
- **[マルチエージェントガイド](docs/guides/0004-multi_agent.md)**：マルチエージェントのシナリオと例
- **[統合ガイド](docs/guides/0005-integrations.md)**：LLM と埋め込みプロバイダーの統合
- **[サブストアガイド](docs/guides/0006-sub_stores.md)**：サブストアの使用方法と例
- **[API ドキュメント](docs/api/overview.md)**：完全な API リファレンス
- **[アーキテクチャガイド](docs/architecture/overview.md)**：システムアーキテクチャと設計
- **[例](docs/examples/overview.md)**：インタラクティブな Jupyter ノートブックとユースケース

## 開発

### 開発環境のセットアップ

```bash
# リポジトリをクローン
git clone https://github.com/powermem/powermem.git
cd powermem

# 開発依存関係をインストール
pip install -e ".[dev,test]"
```

## 貢献

貢献を歓迎します！貢献ガイドラインと行動規範をご覧ください。

## サポート

- **問題報告**：[GitHub Issues](https://github.com/oceanbase/powermem/issues)
- **ディスカッション**：[GitHub Discussions](https://github.com/oceanbase/powermem/discussions)

---

## ライセンス

このプロジェクトは Apache License 2.0 の下でライセンスされています - 詳細については [LICENSE](LICENSE) ファイルを参照してください。

