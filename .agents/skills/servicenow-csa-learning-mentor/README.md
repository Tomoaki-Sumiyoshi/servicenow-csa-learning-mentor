# ServiceNow CSA Learning Mentor

ServiceNow 未経験者が、8週間・週10時間前後で CSA の知識と基本的な管理操作を並行して学ぶための、リポジトリ固有 Codex Agent Skill です。

## 利用開始

前提は Python 3.10 以上、Codex、学習用の ServiceNow Personal Developer Instance（PDI）または非本番インスタンスです。本番環境では演習しないでください。

1. このワークスペースを Codex で開く。
2. 次のように Skill を明示して開始する。

```text
$servicenow-csa-learning-mentor 今日の学習を始める
```

初回は Week 1 から始まり、ワークスペースの `.servicenow-csa-learning/progress.json` が初期状態から自動作成されます。以後は同じ入力で、保存された現在 Week、未完了 Topic、課題、Hint、Quiz、弱点から再開します。

Skill が一覧へすぐ現れない場合は Codex を再起動し、`$servicenow-csa-learning-mentor` を再度指定してください。

## 主な入力

| 入力 | 内容 |
| --- | --- |
| `今日の学習を始める` | 前回の続きから日次セッションを開始 |
| `今日のToDo` | 今日分だけを表示 |
| `進捗` | 8週間全体の進捗を表示 |
| `復習` | 弱点を優先して復習 |
| `問題出して` | 現在の既習範囲から3〜5問を出題 |
| `ハンズオン` | 現在 Topic の実操作課題を開始 |
| `ヒント` | 現在課題の次の Hint を1段階だけ表示 |
| `答え` | 現在課題の具体手順を表示 |
| `CSA対策` | 既習範囲から CSA 形式を意識した独自問題を出題 |
| `弱点` | 保存済みの弱点と復習候補を表示 |
| `今週のゴール` | 今週の到達目標と残タスクを表示 |

表記を完全に合わせる必要はありません。たとえば「前回の続き」「ACL を復習したい」のような自然な依頼にも対応します。

## 進捗を確認する

通常は Codex が管理スクリプトを呼び出します。手動確認する場合はワークスペースルートで実行してください。

```powershell
python .\.agents\skills\servicenow-csa-learning-mentor\scripts\progress.py validate --template
python .\.agents\skills\servicenow-csa-learning-mentor\scripts\progress.py init
python .\.agents\skills\servicenow-csa-learning-mentor\scripts\progress.py status
python .\.agents\skills\servicenow-csa-learning-mentor\scripts\progress.py resume
```

`init` は進捗が存在しない場合だけ作成し、既存状態を上書きしません。ライブ進捗は Skill 本体の外に置かれるため、Skill ファイルを更新しても学習履歴を分離して保持できます。別の状態ファイルで試す場合は、コマンドの前に `--file <path>` を指定してください。

進捗が壊れている場合、スクリプトは上書きせずに失敗します。エラー内容を確認し、既存ファイルをバックアップしてから修復してください。認証情報、Cookie、トークン、実データは進捗へ記録しないでください。

## 教材と情報の扱い

ServiceNow University の日本語コンテンツを優先します。ただし教材名、画面名、製品仕様、CSA 試験情報は変わり得るため、Skill は現在の公式情報を確認し、確認できない内容を推測しません。University へのログインが必要で確認できない場合は、固定リンクを作り上げず、検索語と学ぶべき概念を案内します。
