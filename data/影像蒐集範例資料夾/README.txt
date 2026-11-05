# 影像蒐集範例資料夾（Mock Data，僅供格式參考）

版本：v2（2026/10/27，依標註規則 v0.2 更新；v1 原檔存於 _v1備份/）

這個資料夾裡的所有圖片都是程式產生的示意圖（簡單色塊 + 文字標籤），
**不是真實拍攝的照片**，只用來示範：
1. 資料夾結構長什麼樣子
2. 檔名規則該怎麼命名
3. YOLO 標註格式（.txt）該怎麼寫、內容對應哪個 class_id

正式蒐集時請用這個資料夾的結構放真實照片，把這裡的範例檔刪掉即可。
示意圖的框只示範格式，框的範圍請以下方「標註規則 v0.2」為準。

## 結構說明

- raw/task_A_alert/　→ 對應「工作一：警示物件影像」的原始檔案（目前唯一進行中的工作）
- raw/task_B_marker/ → 對應「工作二：跟隨標記布料影像」（暫停：跟隨模組已移除，範例保留供參考）
- raw/task_C_fall/　 → 對應「工作三：跌倒姿態影像」（待確認是否納入）
- images/{train,val,test}/　→ 切分好、要拿去訓練的圖片（從 raw 複製過來）
- labels/{train,val,test}/　→ 跟 images 同名的 YOLO 格式標註檔（.txt）
- classes.txt　→ class_id 對照表，第幾行（從0起算）就是第幾個 class_id

## 檔名與流水號（task_A）

- 格式：alert_地點_時段_序號.jpg；地點 gate／road／bg，時段 am／noon／pm
- 流水號為 raw/task_A_alert/ 整個資料夾共用、4 位數、不歸零
- 現有資料（task_A_alert_20261013）流水號 0001–0700，新影像從 0701 開始
- 0295–0312 共 18 張為 0 byte 毀損檔，已確認不使用，流水號不重複使用
- 另有 18 張為 test 區塊前後各 3 張的緩衝影像（split=buffer，防止相鄰影格洩漏到 test），刻意不上傳：
  road 0208–0210、0241–0243、0478–0480、0511–0513；bg 0619–0621、0637–0639
  → 700 − 18（毀損）− 18（緩衝）= 664 張，即 Roboflow v2 的張數
- 注意：現有 alert_bg_am_0562–0700 雖命名為 bg，畫面中含公車、汽車、機車、腳踏車與路人，
  且已標註；新拍的 bg 依分配表 v2 定義為無目標背景（.txt 為空）

## YOLO 標註格式

每張圖對應一個同名 .txt，一行一個物件：

    class_id x_center y_center width height

四個數值都是 0–1 的 normalize 比例值（相對於圖片寬高），不是像素座標。
無目標的背景負樣本（例如 alert_bg_noon_0003.jpg）.txt 檔案可以是空的；在 Roboflow 用 Mark Null。

## class 對照（對應 classes.txt，與 Roboflow 專案 alert-yolo-v1 匯出順序一致）

    0 bicycle      腳踏車車體（騎士不標 person）
    1 bus          公車
    2 car          一般汽車、貨車、廂型車
    3 motorcycle   機車、速克達車體（騎士不標 person）
    4 person       所有行人（被照護者與路人規則相同）

訓練時在 Colab 將 bicycle、bus、car、motorcycle 合併為 dynamic_obstacle（2 類：person、dynamic_obstacle）。

## 標註規則 v0.2（摘要）

- person 框「鞋底到膝蓋」，含雙腳與護腕；車輛框住整個可見車體
- 低於 24 px（640×480 原圖）的目標不標；被前景遮擋大半的目標不標
- 只用矩形框，不用 polygon

## 範例標註檔說明

- alert_* 的 .txt 已改為上方 5 類的 class_id
- marker_*、fall_* 的 .txt 仍為 v1 舊類別（0 person、1 marker），因工作二暫停、工作三待確認，未更新，請勿當作現行格式參考
