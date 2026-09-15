# 8週間カリキュラム

このファイルは、週の計画、到達判定、または当日の課題を組み立てるときに、現在週の節だけを参照する。画面名、教材名、試験範囲の最新性は `freshness-and-sources.md` に従って確認する。

## 目次

- [全期間の設計原則](#全期間の設計原則)
- [Week 1：ServiceNowの全体像・基本操作](#week-1servicenowの全体像基本操作)
- [Week 2：データモデル・フォーム](#week-2データモデルフォーム)
- [Week 3：User・Group・Role・Security](#week-3usergrouprolesecurity)
- [Week 4：UI・通知・レポート](#week-4ui通知レポート)
- [Week 5：Flow・Import・Update Set](#week-5flowimportupdate-set)
- [Week 6：総合ハンズオン](#week-6総合ハンズオン)
- [Week 7：CSA試験範囲の総復習](#week-7csa試験範囲の総復習)
- [Week 8：CSA直前対策](#week-8csa直前対策)

## 全期間の設計原則

- 8週間、週10時間前後を基準にし、保存済みの進捗と実際に使える時間に合わせて1日の量を調整する。
- 「教材で学ぶ → ServiceNow上で操作する → 教材を見ずに再現する」を各トピックの基本サイクルにする。
- 全期間では、CSA対策を約40%、ServiceNow上の操作を約60%にする。Week 7〜8で試験対策が増えても、必ず実操作確認を残す。
- ServiceNow Universityの日本語コンテンツを主教材にする。固定のコース名を前提にせず、現在の公式カタログで該当概念を探す。
- 一般的なWeb技術を教え直さず、学習者のフロントエンド実務経験を足場にしてServiceNow固有の差分を説明する。
- 週の完了を日数だけで決めない。到達目標を知識、実操作、教材なしの再現の3面で確認する。
- 先へ進める場合も未達項目を消さず、復習キューへ残す。後続内容の前提になる弱点は先に補強する。

## Week 1：ServiceNowの全体像・基本操作

### 学習内容

- ServiceNow Universityの入門コンテンツ
- Platformの基本構造
- Application Navigator
- Application / Module
- List / Form
- Record / Table / Field
- Filter / Sort
- Incidentなど既存データの操作

### ハンズオン

1. Application Navigatorから目的のModuleを探す。
2. Incident Listを開く。
3. FilterとSortを設定し、結果が変わることを確認する。
4. Recordを作成し、保存後に更新する。
5. ListとFormを往復する。
6. 表示中の画面を使い、Table / Record / Fieldの関係を説明する。

### 教材なしの再現

別の条件でIncidentを絞り込み、1件を登録・更新してから、その操作で扱ったTable、Record、Fieldを説明させる。

### 到達判定

ServiceNowの画面から目的のデータを自力で探し、登録・更新できる。List、Form、Table、Record、Fieldの役割を混同せず説明できる。

## Week 2：データモデル・フォーム

### 学習内容

- Table / Record / Field
- Dictionary
- Field Type
- Reference Field
- Form Layout / List Layout
- Filter

### ハンズオン

1. 練習用Tableを作成する。
2. String、Choice、Referenceを含む複数のFieldを追加する。
3. Reference Fieldを設定し、参照先Recordとの関係を確認する。
4. Form LayoutとList Layoutを変更する。
5. AND / ORを含む複数条件のFilterを作成し、期待結果と照合する。

### 教材なしの再現

別用途の小さなTableを設計し、Field Typeを選んだ理由、Reference先、Form/Listの表示項目を説明して構築させる。

### 到達判定

Table → Field → Record → Form/Listの関係を説明し、簡単なデータ構造と画面構成を自力で作成できる。

## Week 3：User・Group・Role・Security

### 学習内容

- User
- Group
- Role
- Impersonation
- ACL
- Access Controlの基本

### ハンズオン

1. 練習用Userを複数作成する。
2. Groupを作成し、Userを追加する。
3. 適切な練習用Roleを付与する。
4. Impersonateして、Roleによる画面と操作の違いを比較する。
5. 対象TableやFieldの既存ACLを読み、許可条件を確認する。

### 教材なしの再現

2種類の利用者を想定し、User / Group / Roleの構成を作り、Impersonationで差を検証させる。ACLは闇雲に変更せず、まず既存設定を説明させる。

### 到達判定

User / Group / Role / ACLの関係を説明でき、Impersonationを使って基本的な権限差を確認できる。ACLをフロントエンドの表示制御と同一視しない。

## Week 4：UI・通知・レポート

### 学習内容

- Form
- List
- View
- Notification
- Report
- Dashboard

### ハンズオン

1. FormとListの表示項目・配置を変更する。
2. Viewによる表示差を確認する。
3. Notificationを作成し、条件と送信対象を設定する。
4. テスト用RecordでNotificationの発火条件を検証する。
5. Reportを作成し、表示形式を変更する。
6. DashboardへReportを配置する。

### 教材なしの再現

提示された利用者像と通知条件から、必要なForm/List表示、Notification、Report、Dashboardを作成し、各設定の結果を確認させる。

### 到達判定

画面、通知、レポートの基本設定を自力で行い、設定条件と実際の結果を結びつけて説明できる。

## Week 5：Flow・Import・Update Set

### 学習内容

- Flow Designer
- Trigger
- Action
- Import Set
- Transform Map
- Update Set

### ハンズオン

1. Record作成をTriggerとするFlowを作成する。
2. Actionと条件分岐を追加する。
3. Flowをテストし、実行結果を確認する。
4. 練習用CSVをImportする。
5. Transform Mapの対応付けと変換結果を確認する。
6. Update Setを作成し、対象となる設定変更を記録する。

### 教材なしの再現

新しい条件でFlowを組み直し、小さなCSVを投入し、どの変更がUpdate Setに含まれたかを説明させる。

### 到達判定

自動化、データ投入、設定変更管理の基本操作を行い、Trigger / Action、Import Set / Transform Map、変更 / Update Setの関係を説明できる。

## Week 6：総合ハンズオン

### 題材

「問い合わせ管理」の小規模な機能を、可能な限り教材を見ずに構築する。要件を小さく保ち、各構成要素の接続を優先する。

### 必須構成

- Table設計
- Field設計
- User / Group / Role
- Form / List
- ACL
- Notification
- Flow Designer
- Report / Dashboard
- Update Set

### 進め方

1. 問い合わせ、依頼者、担当先、状態、優先度などの最小要件を学習者に整理させる。
2. データモデルと権限方針を先に説明させる。
3. Table、Field、Form、Listを構築させる。
4. User、Group、Role、ACLを設定し、Impersonationで検証させる。
5. NotificationとFlowを追加し、条件別にテストさせる。
6. ReportとDashboardを作成させる。
7. Update Setに記録された変更を確認させる。
8. 別のテストデータで、教材なしの通し再現と説明を行わせる。

### 到達判定

ServiceNow上に簡単な業務機能を一通り構築し、データ、権限、自動化、可視化、変更管理がどう接続するかを説明できる。

## Week 7：CSA試験範囲の総復習

### 復習領域

- Platform Navigation
- Instance Configuration
- Database Management
- Platform Security
- Self-Service
- Automation
- Administration Fundamentals全般

この領域名はカリキュラム上の基準として扱い、現在の公式試験仕様における名称や比重だと断定しない。試験情報を提示する前に公式情報を確認する。

### 3段階確認

各項目を次の順で確認する。

1. **意味**：「これは何か」を自分の言葉で説明させる。
2. **場所**：「どこで設定・確認するか」を案内なしで示させる。
3. **操作**：実際に設定、確認、または結果の検証を行わせる。

間違い、場所の迷い、操作失敗を別々に記録し、該当項目を弱点として復習キューへ入れる。

### 到達判定

主要領域について意味・場所・操作の3段階を通過し、未通過項目が明示された復習計画を持っている。

## Week 8：CSA直前対策

### 重点確認

- Table
- Dictionary
- Reference
- User
- Group
- Role
- ACL
- Import
- Transform
- Flow Designer
- Update Set
- Notification
- Report

### 実施内容

1. 既習範囲からCSA形式を意識した理解度確認を行う。
2. 同じ概念をServiceNow上の操作課題に変換し、知識と操作を往復する。
3. 弱点を優先しつつ、主要項目を偏りなく確認する。
4. 教材なしで主要な管理操作を再現させる。
5. 最新の公式試験情報を確認できた場合のみ、現在の形式・範囲に合わせて最終調整する。

### 到達判定

CSA受験に必要な知識を持ち、主要な管理操作を教材なしで実施できる。未確認の試験仕様や画面名を暗記対象にしていない。
