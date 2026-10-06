# 影像蒐集範例資料夾（Mock Data，僅供格式參考）

這個資料夾裡的所有圖片都是程式產生的示意圖（簡單色塊 + 文字標籤），
**不是真實拍攝的照片**，只用來示範：
1. 資料夾結構長什麼樣子
2. 檔名規則該怎麼命名
3. YOLO 標註格式（.txt）該怎麼寫、內容對應哪個 class_id

正式蒐集時請用這個資料夾的結構放真實照片，把這裡的範例檔刪掉即可。

## 結構說明

- raw/task_A_alert/　→ 對應「工作一：警示物件影像」的原始檔案
- raw/task_B_marker/ → 對應「工作二：跟隨標記布料影像」的原始檔案
- raw/task_C_fall/　 → 對應「工作三：跌倒姿態影像」的原始檔案
- images/{train,val,test}/　→ 切分好、要拿去訓練的圖片（從 raw 複製過來）
- labels/{train,val,test}/　→ 跟 images 同名的 YOLO 格式標註檔（.txt）
- classes.txt　→ class_id 對照表，第幾行（從0起算）就是第幾個 class_id

## YOLO 標註格式

每張圖對應一個同名 .txt，一行一個物件：

    class_id x_center y_center width height

四個數值都是 0–1 的 normalize 比例值（相對於圖片寬高），不是像素座標。
空白物件（例如 alert_bg_noon_0003.jpg 這種背景負樣本）.txt 檔案可以是空的，不需要寫任何一行。

## class 對照（對應 classes.txt）

    0 person        一般行人／跌倒姿態
    1 marker        亮色布料跟隨標記
    2 car           汽車
    3 two_wheeler   機車／腳踏車
