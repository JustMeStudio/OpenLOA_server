import os
import cv2
import numpy as np
from datetime import datetime
from agents.globals.context import PROJECT_NAME, PROJECT_PATH
from agents.utils.com import read_file_from_url
from agents.utils.oss_utils import upload_file_to_oss

#----------------------------------------------------------------------------------------
# Remove background from LOGO image
async def remove_logo_background(image_url: str):
    """
    自动移除LOGO图片的背景，使背景透明
    
    ⭐ 完全自动化：
    - 自动分析图片边缘像素检测背景色
    - 根据背景色亮度智能推荐最优阈值
    - 一键完成，无需手动参数
    
    参数:
    - image_url: 原始图片的URL地址（来自generate_logo_image的输出）
    
    💡 效果最优的条件：
    - LOGO与背景边界清晰分明，无外部发光/投影/阴影/渐变
    - 纯色背景（纯白/纯黑/单一深色）
    """
    
    try:
        # 从URL读取图片
        image_data = await read_file_from_url(image_url, is_binary=True)
        
        if not isinstance(image_data, bytes):
            return {
                "result": "failure",
                "error_type": "read_image_failed",
                "message": f"无法读取图片：{image_data}"
            }
        
        # 将字节数据转换为numpy数组
        nparr = np.frombuffer(image_data, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if img is None:
            return {
                "result": "failure",
                "error_type": "decode_image_failed",
                "message": "无法解码图片，请确保图片格式正确"
            }
        
        # 转换BGR为RGB（OpenCV默认是BGR）
        img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        
        # ★ 自动分析图片边缘像素来检测背景色
        height, width = img_rgb.shape[:2]
        margin = 50
        
        # 采集四个角的像素
        corner_pixels = []
        corner_pixels.extend(img_rgb[0:margin, 0:margin].reshape(-1, 3))
        corner_pixels.extend(img_rgb[0:margin, -margin:].reshape(-1, 3))
        corner_pixels.extend(img_rgb[-margin:, 0:margin].reshape(-1, 3))
        corner_pixels.extend(img_rgb[-margin:, -margin:].reshape(-1, 3))
        
        # 计算边缘像素的平均颜色（假设为背景色）
        corner_pixels = np.array(corner_pixels)
        avg_color = np.mean(corner_pixels, axis=0).astype(int)
        bg_r, bg_g, bg_b = avg_color
        background_color = f"#{bg_r:02x}{bg_g:02x}{bg_b:02x}"
        
        # ★ 根据背景色亮度自动推荐最优阈值
        brightness = (bg_r + bg_g + bg_b) / 3
        if brightness > 200:
            threshold = 25  # 亮色背景，用较严格的阈值
        elif brightness < 50:
            threshold = 30  # 深色背景，用标准阈值
        else:
            threshold = 35  # 中等亮度，用宽松阈值
        
        bg_color_rgb = np.array([bg_r, bg_g, bg_b])
        
        # 创建掩码：找出与背景颜色接近的像素
        # 计算每个像素与背景颜色的欧氏距离
        distance = np.sqrt(np.sum((img_rgb.astype(np.float32) - bg_color_rgb.astype(np.float32)) ** 2, axis=2))
        
        # ★ 将距离映射到 alpha 值（平滑过渡，消除锯齿）
        # 而非硬阈值二值化，这样边缘会自动柔和
        soft_range = threshold * 0.5  # 过渡范围为阈值的 50%
        alpha = np.clip((distance - (threshold - soft_range)) / (soft_range * 2) * 255, 0, 255).astype(np.uint8)
        
        # 应用高斯模糊平滑边缘（进一步消除锯齿）
        alpha = cv2.GaussianBlur(alpha, (3, 3), 0.5)
        
        # 创建RGBA图片（带透明通道）
        img_rgba = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2RGBA)
        
        # 使用映射后的 alpha 值（包含平滑过渡）
        img_rgba[:, :, 3] = alpha
        
        # 保存为PNG文件 - 使用cv2.imencode绕过OpenCV的路径编码问题
        try:
            project_path = PROJECT_PATH.get()
            os.makedirs(project_path, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            save_path = os.path.join(project_path, f"logo_transparent_{timestamp}.png")
            
            # OpenCV需要BGR格式，转换回去
            img_bgra = cv2.cvtColor(img_rgba, cv2.COLOR_RGBA2BGRA)
            
            # 使用 imencode 编码为PNG字节，然后用Python原生文件操作写入
            # 这样可以完全避免OpenCV对Windows中文路径的处理问题
            success, encoded_image = cv2.imencode('.png', img_bgra)
            
            if not success:
                return {
                    "result": "failure",
                    "error_type": "save_image_failed",
                    "message": "PNG编码失败，无法处理图片"
                }
            
            # 将编码后的字节写入文件（使用Python原生写入，避免OpenCV的路径处理）
            with open(save_path, 'wb') as f:
                f.write(encoded_image.tobytes())
        except Exception as e:
            return {
                "result": "failure",
                "error_type": "save_image_failed",
                "message": f"保存处理后的图片失败：{str(e)}"
            }
        
        # 上传到OSS
        file_name = os.path.basename(save_path)
        oss_key = f"user/task_files/{PROJECT_NAME.get()}/{file_name}"
        result_url = await upload_file_to_oss(save_path, oss_key)
        
        return {
            "result": "success",
            "image_url": result_url,
            "file_attachment": result_url,
            "message": f"背景已移除（自动检测：背景颜色 {background_color}, 阈值 {threshold}），图片已转为透明底"
        }
        
    except Exception as e:
        return {
            "result": "failure",
            "error_type": "processing_error",
            "message": f"处理图片失败：{str(e)}"
        }
