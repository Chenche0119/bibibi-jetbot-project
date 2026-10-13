# JetBot WiFi Dongle（Edimax AC1200）硬體診斷測試記錄

## 背景

- 裝置：Edimax AC1200 USB WiFi Adapter（USB ID `7392:f822`，晶片 Realtek，Chip Version `CHIP_8812_Normal_Chip_UMC_E_CUT_2T2R`）
- 主機：Jetson Nano（JetBot），L4T Ubuntu 18.04.5，kernel 4.9.201-tegra
- Driver：`morrownr/8812au-20210820`（已針對 `7392:f822` patch）
- 症狀：driver 嘗試對晶片做 RF 電源開啟時，卡在 register polling：
  ```
  RTW: ERROR HalPwrSeqCmdParsing: Fail to polling Offset[0x5]=01
  RTW: ERROR _InitPowerOn_8812AU: run power on flow fail
  RTW: ERROR rtw_hal_init: fail
  ```
- 前提排除：已確認與 USB autosuspend、電源供應方式（Micro-USB vs Barrel Jack + J48）、driver 版本（測過兩套獨立 codebase）均無關，兩者都重現一致的失敗。

本次針對「開機時序」與「USB 傳輸層真實狀況」做兩階段測試。

---

## 階段一：開機時序測試（冷插拔 / 熱插拔對照）

### 目的
驗證失敗是否肇因於「Nano 開機過程中 USB Hub 初始化時序 / enumeration race」，而非硬體本身不相容。

### 測試方法
1. JetBot 完全斷電，dongle **不插著**開機
2. 確認 SSH 可連線、系統開機完成（`uptime` 顯示 `up 0 min` 確認為全新開機）
3. Linux 完全開機後，才把 dongle **熱插上**
4. 即時監控 `dmesg -w`，觀察 driver 載入（`insmod`）過程

### 結果
- USB 列舉本身正常（`idVendor=7392, idProduct=f822` 正確被偵測）
- **`insmod` 這次乾淨完成，完全沒有出現 `HalPwrSeqCmdParsing` 錯誤訊息**，一度看似時序問題已解決
- 但實際嘗試 `ip link set wlan0 up` 時回傳 `RTNETLINK answers: Operation not permitted`，且 `nmcli device status` 顯示 `wlan0` 卡在 `unavailable`

### 深入排查
- 用背景持續執行的 `dmesg -w` 記錄檔比對，確認 `ip link up` 當下完全沒有觸發任何 kernel log
- 追查 driver 原始碼發現：預設 `rtw_drv_log_level = 0`，把 `RTW_INFO` 等級的訊息全部濾掉，導致看不到真正的初始化過程
- 將 `/sys/module/8812au/parameters/rtw_drv_log_level` 調到 `5`（最大詳細度）後重試，**這次完整重現了原本的失敗**：
  ```
  RTW: _netdev_open(wlan0) , bup=0
  RTW:  power-on :REG_SYS_CLKR 0x09=0x30. REG_CR 0x100=0xea.
  RTW:  MAC has not been powered on yet.
  RTW: ERROR HalPwrSeqCmdParsing: Fail to polling Offset[0x5]=01
  RTW: rtl8812au_hal_init in 696ms
  ```

### 結論
這個 driver 版本的 RF 硬體初始化（`rtw_hal_init`）**並非在 `insmod` 時執行，而是延遲到 `ifconfig up` / `ip link set up` 那一刻才真正觸發**。之前「insmod 成功」只是假象，不是時序問題被解決，而是同一個失敗被搬到後面的步驟才發生。

**「開機時序 / USB 熱插拔」這個假說已被直接證據排除**：不論 dongle 是開機前就插著、還是開機後才插入，只要真正嘗試打開 RF，都在 470–700ms 內卡在同一個 register，結果完全一致。

---

## 階段二：逾時延長測試 + USB 封包層級截取（usbmon）

### 2a. 延長 polling 逾時

**目的**：確認是否為「差一點時間就會成功」的邊緣時序問題，而非真正卡死。

**方法**：修改 driver 原始碼 `hal/HalPwrSeqCmd.c`，將 offset 0x5 這條 polling 路徑的 `maxPollingCnt` 從預設 `5000` 改為 `50000`（逾時時間從約 470ms 拉長至約 4.8 秒，10 倍），重新編譯、重新載入 driver 後重試。

**結果**：
```
RTW: ERROR HalPwrSeqCmdParsing: Fail to polling Offset[0x5]=01
RTW: rtl8812au_hal_init in 4804ms
```
等了 10 倍的時間，結果依然一致失敗。**排除「邊緣時序、再等久一點就會過」的可能性。**

### 2b. usbmon 封包截取

**目的**：確認卡住的根因是 USB 傳輸層（協定層錯誤、逾時、stall）還是晶片內部狀態本身卡死。

**方法**：
1. `sudo modprobe usbmon`，掛載 debugfs
2. 對 dongle 所在的 USB Bus（Bus 001，dongle 列舉為 `Device 003`）即時擷取 `/sys/kernel/debug/usb/usbmon/1u`
3. 同步重新載入 driver、觸發 `ip link set wlan0 up`，讓完整的 4.8 秒 polling 過程被完整記錄
4. 篩選出所有針對 register offset `0x0005` 的讀取（USB control transfer setup packet：`bRequestType=c0, bRequest=05, wValue=0005`），統計每次讀回的數值

**結果**：
```
200020 次 USB control transfer 讀取
→ 全部成功完成（status = 0，無 timeout、無 stall、無協定層錯誤）
→ 讀回數值 100% 都是 "01"，沒有任何一次變化
```

### 結論
- **USB 傳輸層完全正常**：每一次 control transfer 都確實送達晶片、確實收到回應，通訊協定本身沒有任何問題
- **問題出在晶片內部的硬體狀態機**：offset 0x5 這個 register（推測是 RF / 時脈就緒狀態旗標）在晶片內部從頭到尾卡在 `01`，driver 預期它會翻轉成別的值，但晶片端的狀態從未真正轉換

---

---

## 重現驗證（2026-10-13 獨立重跑一次）

為排除「上次剛好遇到某種暫態雜訊」的可能性，隔日用完全獨立的一次開機週期，把階段一、二a、二b 三項測試重跑一次（driver 使用上次已 patch 好的 `maxPollingCnt=50000` 版本，load 時直接帶 `rtw_drv_log_level=5`，一次到位不用再補調）。

### 重現結果

| 項目 | 這次（10/13） | 上次（原始測試） | 一致？ |
|---|---|---|---|
| 熱插拔時序（開機後才插 dongle） | `Offset[0x5]=01` 失敗，4004ms / 4748ms | 同樣失敗（舊逾時版本下 696ms） | ✅ |
| 延長逾時（50000 次，約 4.8 秒） | 一樣卡住，從未通過 | 一樣卡住，4804ms | ✅ |
| usbmon 封包截取 | 200,020 次讀取，100% 回傳 `01` | 200,020 次讀取，100% 回傳 `01` | ✅ 完全一致，連次數都一樣 |

### 結論
同一台 JetBot、同一顆 dongle，在不同次開機、不同次測試下，行為**完全可重複**，USB 傳輸層依舊健康，晶片內部 register 依舊卡在 `01`。這排除了「單次測試遇到暫態雜訊/巧合」的可能性，確認是穩定、可重現的硬體層級故障。

---

## 總結：根因定位

| 已排除的假說 | 排除方式 |
|---|---|
| USB autosuspend / 電源管理 | 已關閉測試，結果不變 |
| 電源供應不足（Micro-USB） | 換 Barrel Jack + J48（5V/4A）測試，結果不變 |
| Driver 程式碼問題 | 兩套獨立 codebase（aircrack-ng / morrownr）測試，結果一致 |
| 開機 USB 初始化時序 / enumeration race | 冷開機 vs 熱插拔對照測試，結果一致（階段一） |
| Polling 逾時設太短，需要更久才穩定 | 逾時拉長 10 倍（470ms→4.8s），結果不變（階段二 2a） |
| USB 傳輸層錯誤（timeout/stall/協定錯誤） | usbmon 截取 20 萬次真實封包，傳輸 100% 成功（階段二 2b） |

**最終結論**：這是 Edimax AC1200（chip `UMC_E_CUT`）與這台 Jetson Nano 之間，在 RF 電源開啟階段的**晶片級硬體不相容**，與 driver 版本、開機時序、逾時設定、電源來源皆無關。已超出遠端軟體診斷可處理的範圍。

## 後續可行方向（待硬體）

1. **主動供電 USB Hub**：讓 dongle 走獨立電源，排除任何 Nano USB port 本身電流供應的邊際因素
2. **更換低功耗 WiFi dongle**（如 RTL8188EUS 系列）：與 Nano 相容性紀錄較好的常見替代方案

目前手邊皆無這兩項硬體，暫不便採購，待取得後再繼續 Task #3（`nmtui` 實際連線測試）。
