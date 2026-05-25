# VRM 表情能力調查

更新日期：2026-03-24

## 結論

目前專案內使用的 4 個 VRM 模型本身都有表情資料，但 3D 舞台 runtime 尚未接入表情控制邏輯。

也就是說：

- 模型端：有支援
- 前端 3D 舞台：尚未使用

## 調查範圍

檔案位置：

- `agents-stage-live2d-vrm3d-fe/public/vrm3d/AliciaSolid.vrm`
- `agents-stage-live2d-vrm3d-fe/public/vrm3d/avatar_L.vrm`
- `agents-stage-live2d-vrm3d-fe/public/vrm3d/ふらすこ式風きりたん_VRM_1_0_1.vrm`
- `agents-stage-live2d-vrm3d-fe/public/vrm3d/HatsuneMikuNT.vrm`

調查方式：

1. 掃描 `.vrm` 二進位內是否存在 `blendShape` / expression 關鍵字
2. 直接解析 GLB JSON chunk，讀取 `extensions.VRM.blendShapeMaster.blendShapeGroups`
3. 檢查 3D 舞台程式是否有呼叫 expression / blendshape 相關 API

## 模型調查結果

4 個模型皆解析出 `VRM0` 的 `blendShapeMaster.blendShapeGroups`。

所有模型目前都至少包含以下 preset：

- `neutral`
- `a`
- `i`
- `u`
- `e`
- `o`
- `blink`
- `joy`
- `angry`
- `sorrow`
- `fun`
- `lookup`
- `lookdown`
- `lookleft`
- `lookright`
- `blink_l`
- `blink_r`

這表示模型層已具備：

- 基本情緒表情
- 眼睛表情
- 視線方向
- 簡單嘴型 viseme

## 前端 3D runtime 現況

檔案：

- `agents-stage-live2d-vrm3d-fe/src/components/session-stage/useVrmStage.runtime.ts`

目前已存在：

- VRM 載入
- `AnimationMixer`
- VRMA 動作播放
- 漫遊 / 跳躍 / 行為流
- 角色 head label / camera focus / interaction point

目前未看到：

- `expressionManager`
- `blendShapeProxy`
- `getExpression`
- `setValue(...)` 這類表情權重控制

目前 `playActorMotion(...)` 只處理 VRMA 動作，不處理表情：

- `useVrmStage.runtime.ts` 約 `1083` 行附近

建立角色時也只有載入 VRM、建立 mixer、掛 root，不包含表情控制器初始化：

- `useVrmStage.runtime.ts` 約 `1268` 行附近

## 對後續實作的意義

後續如果要做 3D 表情功能，不需要先換模型，直接在現有模型上接控制即可。

可優先做的功能順序：

1. 手動表情切換 UI
2. 依 session state 自動切換情緒表情
3. 聊天輸出時加入嘴型 `a/i/u/e/o`
4. 視線 / blink 與互動點、鏡頭聚焦整合

## 建議實作方向

### 方案 A：先做最小可用版

目標：

- 可手動切換 `joy / angry / sorrow / fun / blink`
- 可重置回 `neutral`

建議做法：

- actor 建立時把表情控制器掛到 `VrmActor`
- 新增 `setActorExpression(actor, presetName, weight)` helper
- 在 3D 舞台左上角設定或 actor 面板加入測試按鈕

### 方案 B：接 session state 自動表情

可映射方向：

- `RESPONDING` -> `joy` 或 `fun`
- `THINKING` -> `neutral`
- `WAITING` -> `sorrow` 或較柔和表情
- `TOOLING` -> `neutral`
- `error` 類狀態 -> `angry` 或 `sorrow`

### 方案 C：嘴型同步

因模型已有 `a/i/u/e/o`，可以先做簡易版：

- 依 TTS 字元或分詞粗略切換 viseme
- 不做精準 phoneme alignment，先求有感

## 目前已知限制

1. 雖然模型有 `lookup/lookdown/lookleft/lookright`，但是否要與現有 camera focus / head tracking 共用，需要另外設計
2. `blink` 若同時由 runtime 自動眨眼與手動表情控制，需處理權重疊加
3. VRM0 與 VRM1 API 命名不同；目前專案內這 4 個檔案看起來都是 VRM0，但後續若混入 VRM1，要做 adapter

## 建議後續檢查點

開始實作前再確認：

1. `@pixiv/three-vrm` 目前版本對 VRM0 expression 的實際 API 形態
2. 是否要把 expression 狀態納入 `VrmActor` 結構
3. 表情切換是否需要淡入淡出與互斥規則
