# AskInsight — AI駆動データインサイトエージェント

[![License](https://img.shields.io/badge/license-Apache%202.0-green.svg)](../LICENSE)

> エンタープライズ級NL2SQLシステム — 自然言語をSQLに変換し、自動実行・可視化。
> Apache Doris専用設計、MySQL/PostgreSQLマルチデータソース対応。

---

## ✨ 主な機能

| モジュール | 機能 |
|------|------|
| **NL2SQLエンジン** | 26ノードLangGraphワークフロー、93.3%精度 |
| **FK→PK自動推論** | 外部キー関係を自動検出、手動設定不要 |
| **RRF 3-way融合検索** | フィールド+メトリクス+値、k=60 |
| **複雑度グレーディング** | 3段階ルーティング：自動/検証/降格 |
| **導入前スコアリング** | 5次元強制評価、70点未満でクエリロック |
| **セキュリティ** | 15種SQLインジェクション防止 + パスホワイトリスト |

---

## 🚀 クイックスタート

```bash
git clone https://github.com/Hesqeria/AskInsight.git
cd AskInsight
cp .env.example .env
docker compose up -d
make init
```

---

## 📖 ドキュメント

- [English README](../README.md)
- [中文 README](README_CN.md)
- [API Docs](http://localhost:8000/docs)
