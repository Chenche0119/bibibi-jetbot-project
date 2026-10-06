### **一、 核心關鍵：釐清 Nano 與 Orin 的硬體與軟體代溝**

在開始安裝前，必須先確認手上的硬體規格。這兩代晶片的系統基礎完全不同：

| 平台 | 最頂支援版本 | 作業系統 | Python 預設 | 轉換建議 |
| :---- | :---- | :---- | :---- | :---- |
| **舊款 Jetson Nano (Maxwell 架構)** | **JetPack 4.6.x**（官方已停更） | Ubuntu 18.04 | Python 3.6 | **地獄級相容陷阱**。官方 PyTorch 只到 1.10，但 Ultralytics YOLOv8 強制要求 Python \$\\ge\$ 3.8 與 PyTorch \$\\ge\$ 1.8。必須升級 Python 3.8 或走 ONNX 導出。 |
| **Jetson Orin 系列 (Orin Nano / NX)** | **JetPack 5.1.x / 6.x** (Ampere 架構) | Ubuntu 20.04 / 22.04 | Python 3.8 / 3.10 | **開箱即用**。原生支援現代 PyTorch、Torchvision 與 TensorRT 8.x，流程極為順暢。 |

**最嚴重的依賴巨坑（千萬別做）**：  
絕對**不要**在 Jetson 上直接執行 pip install torch torchvision！這會從 PyPI 下載 x86 架構或未針對 Jetson 硬體編譯的通用輪子（Wheel），導致找不到 CUDA 支援（torch.cuda.is\_available() 變成 False）。必須下載 NVIDIA 官方編譯好的 Jetson 特規 .whl。

### **二、 環境依賴避坑配置（Step-by-Step）**

#### **1\. 確保板端 Swap 記憶體足夠（防止編譯時 OOM 當機）**

TensorRT 構建引擎需要消耗大量記憶體。4GB 記憶體的 Nano / Orin Nano 極易被 Linux OOM-Killer 強制中斷，轉換前必須掛載至少 4GB～8GB 的 Swap：

Bash  
\# 安裝並啟用 zram 或建立 6GB 交換空間  
sudo fallocate \-l 6G /swapfile  
sudo chmod 600 /swapfile  
sudo mkswap /swapfile  
sudo swapon /swapfile  
\# 寫入 fstab 永久生效  
echo '/swapfile none swap sw 0 0' | sudo tee \-a /etc/fstab

#### **2\. 安裝 NVIDIA 官方專用 PyTorch 與 Torchvision**

請根據你的 JetPack 版本（可用 jtop 或 cat /etc/nv\_tegra\_release 查詢）下載對應輪子。

* **如果是 JetPack 5.x（Orin Nano 常見）：**

Bash  
\# 1\. 系統相依庫  
sudo apt-get update  
sudo apt-get install \-y libopenblas-base libopenmpi-dev libomp-dev

\# 2\. 安裝 NVIDIA 官方 PyTorch v2.1.0 (Python 3.8 範例)  
wget https://developer.download.nvidia.com/compute/redist/jp/v512/pytorch/torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux\_aarch64.whl  
pip3 install torch-2.1.0a0+41361538.nv23.06-cp38-cp38-linux\_aarch64.whl

\# 3\. 編譯安裝對應版本的 Torchvision (例如 v0.16.0)  
sudo apt-get install \-y libjpeg-dev zlib1g-dev libpython3-dev libavcodec-dev libavformat-dev libswscale-dev  
git clone \--branch v0.16.0 https://github.com/pytorch/vision torchvision  
cd torchvision  
export BUILD\_VERSION=0.16.0  
python3 setup.py install \--user  
cd ..

#### **3\. 解決 TensorRT Python 綁定丟失問題**

如果使用虛擬環境（venv / conda），常會發現 import tensorrt 報錯 ModuleNotFoundError。這是因為 JetPack 預設將 TensorRT 的 Python 庫安裝在系統全域路徑下。

* **修復方式（軟連結）：**

Bash  
\# 檢查全域是否存在 tensorrt.so  
ls /usr/lib/python3.\*/dist-packages/tensorrt\*

\# 若在虛擬環境中，直接建立符號連結：  
ln \-s /usr/lib/python3.8/dist-packages/tensorrt\* \~/.virtualenvs/myenv/lib/python3.8/site-packages/

#### **4\. 安裝輕量化的 Ultralytics**

為避免 pip install ultralytics 自動覆蓋掉剛裝好的 Jetson 專用 PyTorch，使用 \--no-deps 參數安裝：

Bash  
pip3 install ultralytics \--no-deps  
\# 手動補齊必要的純 Python 相依模組（排除 torch, torchvision）  
pip3 install pyyaml tqdm matplotlib pillow scipy requests seaborn psutil pandas

### **三、 YOLOv8 轉 TensorRT (FP16) 完整操作流程**

#### **方案 A：使用 Ultralytics 原生指令（最推薦，適用於 Orin Nano）**

在板端直接執行導出。注意：**務必加上 half=True 開啟 FP16**，這能利用 TensorCores 獲得 2\~3 倍的速度提升，顯存直接減半。

Bash  
\# 命令列一鍵轉換（以 yolov8n-pose 為例）  
yolo export model=yolov8n-pose.pt format=engine half=True device=0 imgsz=640

\# 或使用 Python 腳本導出

Python  
from ultralytics import YOLO

\# 載入 PyTorch 權重  
model \= YOLO("yolov8n-pose.pt")

\# 導出為 TensorRT 引擎 (FP16 精度、指定輸入尺寸)  
model.export(format\="engine", half=True, device=0, imgsz=640)

執行後會在當前目錄生成 yolov8n-pose.engine。

#### **方案 B：舊款 Jetson Nano (JetPack 4.6) 的解法：PC 端導出 ONNX \$\\rightarrow\$ 板端 trtexec**

舊版 Nano 無法正常運行新版 Ultralytics 的導出模組。標準工作流程是「**在 PC 上導出 ONNX，移至 Jetson Nano 用原生工具編譯**」：

> 1. **在具備 GPU 的電腦/伺服器上導出 ONNX：**  
>    Python  
>    from ultralytics import YOLO  
>    model \= YOLO("yolov8n-pose.pt")  
>    \# 導出包含靜態 Batch Size 的 ONNX  
>    model.export(format\="onnx", dynamic=False, simplify=True, imgsz=640)

>    *產生 yolov8n-pose.onnx，並將其透過 scp 傳至 Jetson Nano。*  
> 2. **在 Jetson Nano 板端使用 NVIDIA 原生 trtexec 編譯引擎：注意**：TensorRT 引擎（.engine）具有**硬體特異性**，無法在 PC 編譯後拷貝給 Jetson 跑，**必須在板端硬體上產生**！  
>    Bash  
>    /usr/src/tensorrt/bin/trtexec \\  
>      \--onnx=yolov8n-pose.onnx \\  
>      \--saveEngine=yolov8n-pose.engine \\  
>      \--fp16 \\  
>      \--workspace=2048

### **四、 推論驗證與工程程式碼範例**

轉換完成後，直接在 Jetbot 的 Python 控制腳本中調用 .engine：

Python  
import cv2  
from ultralytics import YOLO

\# 載入 TensorRT 引擎  
model \= YOLO("yolov8n-pose.engine")

cap \= cv2.VideoCapture(0)  \# CSI 或 USB 鏡頭

while cap.isOpened():  
    ret, frame \= cap.read()  
    if not ret:  
        break

    \# 推論 (傳入 TensorRT 引擎)  
    results \= model.predict(frame, imgsz=640, half=True, verbose=False)

    for r in results:  
        boxes \= r.boxes.xyxy.cpu().numpy()       \# Bounding Box 座標  
        keypoints \= r.keypoints.data.cpu().numpy() \# 17 處人體骨架點 (x, y, conf)

        \# 這裡插入你的手勢狀態機與 PID 馬達控制邏輯  
        \# print("偵測到人體框:", boxes)

    cv2.imshow("Jetbot Feed", frame)  
    if cv2.waitKey(1) & 0xFF \== ord('q'):  
        break

cap.release()  
cv2.destroyAllWindows()

### **五、 避坑檢查清單（Checklist）**

> 1. **引擎不可跨晶片攜帶**：在 Orin 上編譯出的 .engine 不能拿去 Nano 跑；在 PC 上編譯的也不能拿給 Jetson。  
> 2. **輸入維度固定（Static vs Dynamic）**：在邊緣運算上，盡量使用固定的影像尺寸（例如 imgsz=640 或 imgsz=416）。固定尺寸能讓 TensorRT 在編譯期選擇最優的核心計算策略（Kernel Tiling），效能明顯優於動態維度。  
> 3. **風扇與效能模式鎖定**：  
>    在執行轉換與正式跟隨前，務必將 Jetson 算力全開，否則可能降頻導致推論卡頓：  
>    Bash  
>    sudo nvpmodel \-m 0       \# 切換至最大功率模式 (MAXN)  
>    sudo jetson\_clocks       \# 鎖定 CPU / GPU 最高時脈與風扇轉速  
