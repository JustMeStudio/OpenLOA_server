"""
通用 HTML 转换模块 - HTML to PDF/PNG 转换工具
"""
import os
import re
import uuid
import asyncio
import requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from agents.globals.context import PROJECT_NAME, PROJECT_PATH
from agents.utils.oss_utils import upload_file_to_oss

# 全局 PDF 转换线程池，支持多并发
_pdf_executor = ThreadPoolExecutor(
    max_workers=min(10, (os.cpu_count() or 1) + 4)  # CPU核心数+4，最多10个线程
)

# 全局 PNG 转换线程池，支持多并发
_png_executor = ThreadPoolExecutor(
    max_workers=min(10, (os.cpu_count() or 1) + 4)  # CPU核心数+4，最多10个线程
)

async def html_to_pdf(
    url: str,
    pdf_config: dict = None,
) -> dict:
    """
    将 HTML URL 转换为 PDF（主入口函数）
    
    Args:
        url: HTML 文件的网络 URL
        pdf_config: PDF 生成配置，支持以下参数：
            {
                "format": "A4" | "Letter",  # 可选，默认 None（使用自定义尺寸）
                "width": "1280px",           # 可选
                "height": "720px",           # 可选
                "landscape": True,           # 可选，默认 False
                "print_background": True,    # 可选，默认 True
                "margin": {...},             # 可选，默认全 0
                "viewport_width": 1280,      # 可选，默认 1280
                "viewport_height": 800,      # 可选，默认 800
                "device_scale_factor": 2,    # 可选，默认 2
                "wait_until": "load",        # 可选，可选值 "load" | "domcontentloaded" | "networkidle"
                "extra_wait_ms": 1000        # 可选，额外等待时长（毫秒）
            }
    
    Returns:
        {
            "result": "success",
            "file_attachment": "PDF的网络URL"
        }
        或
        {
            "result": "error",
            "message": "错误描述"
        }
    """
    if pdf_config is None:
        pdf_config = {}
    
    # 设置默认值
    default_config = {
        "viewport_width": 1280,
        "viewport_height": 800,
        "device_scale_factor": 2,
        "print_background": True,
        "margin": {"top": "0", "right": "0", "bottom": "0", "left": "0"},
        "wait_until": "load",
        "extra_wait_ms": 1000
    }
    # 合并用户配置
    final_config = {**default_config, **pdf_config}
    
    loop = asyncio.get_event_loop()
    
    try:
        pdf_path = await loop.run_in_executor(
            _pdf_executor,
            _html_to_pdf_sync,
            url,
            final_config
        )
        
        if isinstance(pdf_path, dict) and pdf_path.get("result") == "error":
            return pdf_path
        
        # PDF 生成成功，进行异步上传
        parsed_url = urlparse(url)
        file_name_base = os.path.basename(parsed_url.path) or "document.html"
        file_name_no_ext = os.path.splitext(file_name_base)[0]
        oss_key = f"user/task_files/{PROJECT_NAME.get()}/{file_name_no_ext}.pdf"
        updated_pdf_url = await upload_file_to_oss(pdf_path, oss_key)
        
        # 清理本地文件
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except Exception as e:
            print(f"删除临时PDF文件失败: {str(e)}")
        
        return {
            "result": "success",
            "pdf_save_path": updated_pdf_url,
            "file_attachment": updated_pdf_url
        }
    
    except Exception as e:
        print(f"PDF转换失败: {str(e)}")
        return {"result": "error", "message": str(e)}


def _html_to_pdf_sync(url: str, config: dict) -> dict | str:
    """
    同步版本的 HTML→PDF 转换函数
    在线程池中运行，支持多种 PDF 格式配置
    
    Args:
        url: HTML 文件的网络 URL
        config: PDF 生成配置字典
    
    Returns:
        成功返回 PDF 文件路径（字符串），失败返回错误字典
    """
    # 1. 生成临时文件路径
    temp_dir = os.path.join(PROJECT_PATH.get(), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = str(uuid.uuid4())
    output_pdf_path = os.path.join(temp_dir, f"render_{unique_id}.pdf")
    
    try:
        with sync_playwright() as p:
            # 2. 启动浏览器
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                print(f"Chromium 启动失败: {str(e)}，正在尝试自动安装...")
                import subprocess
                import sys as sys_module
                try:
                    subprocess.run(
                        [sys_module.executable, "-m", "playwright", "install", "chromium"],
                        check=True
                    )
                    browser = p.chromium.launch(headless=True)
                except Exception as install_error:
                    return {
                        "result": "error",
                        "message": f"Chromium 安装或启动失败: {str(install_error)}"
                    }
            
            # 3. 配置 Context 和 Page
            viewport_config = {
                'width': config.get('viewport_width', 1280),
                'height': config.get('viewport_height', 800)
            }
            with browser.new_context(
                viewport=viewport_config,
                device_scale_factor=config.get('device_scale_factor', 2),
                accept_downloads=False
            ) as context:
                with context.new_page() as page:
                    # 4. 通过 requests 获取远程 HTML 内容
                    print(f"正在拉取 HTML 内容: {url}")
                    page.emulate_media(media="screen")
                    try:
                        headers = {
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                            "Accept-Encoding": "gzip, deflate, br",
                            "DNT": "1",
                            "Connection": "keep-alive",
                            "Upgrade-Insecure-Requests": "1",
                            "X-Source-ID": "internal"
                        }
                        resp = requests.get(url, timeout=30, headers=headers)
                        resp.encoding = 'utf-8'
                        html_text = resp.text
                        wait_until = config.get('wait_until', 'load')
                        page.set_content(html_text, wait_until=wait_until)
                    except Exception as e:
                        return {"result": "error", "message": f"访问HTML页面失败: {str(e)}"}
                    
                    # 5. 渲染优化：等待网络静默、字体、图片全部加载完成
                    try:
                        page.wait_for_load_state("networkidle", timeout=30000)
                        page.evaluate("() => document.fonts.ready")
                        # 等待所有图片加载完成
                        page.evaluate("""() => {
                            const imgs = Array.from(document.images);
                            return Promise.all(imgs.map(img => img.complete ? Promise.resolve() : new Promise(resolve => {
                                img.onload = resolve;
                                img.onerror = resolve;
                            })));
                        }""")
                        # 额外缓冲时间
                        extra_wait = config.get('extra_wait_ms', 1000)
                        page.wait_for_timeout(extra_wait)
                    except Exception as e:
                        print(f"渲染优化出现非致命错误: {str(e)}")
                    
                    # 6. 生成 PDF（根据配置选择不同的参数）
                    try:
                        pdf_args = {
                            "path": output_pdf_path,
                            "print_background": config.get('print_background', True),
                            "margin": config.get('margin', {"top": "0", "right": "0", "bottom": "0", "left": "0"}),
                            "prefer_css_page_size": True,
                            "display_header_footer": False
                        }
                        
                        # 根据配置选择 format 或 width/height/landscape
                        if config.get('format'):
                            # 使用标准格式（如 A4、Letter）
                            pdf_args['format'] = config.get('format')
                        else:
                            # 使用自定义尺寸
                            if config.get('width'):
                                pdf_args['width'] = config.get('width')
                            if config.get('height'):
                                pdf_args['height'] = config.get('height')
                            if config.get('landscape') is not None:
                                pdf_args['landscape'] = config.get('landscape')
                        
                        page.pdf(**pdf_args)
                        return output_pdf_path
                    except Exception as e:
                        return {"result": "error", "message": f"PDF生成失败: {str(e)}"}
    
    except Exception as e:
        print(f"PDF转换发生意外错误: {str(e)}")
        return {"result": "error", "message": f"PDF转换失败: {str(e)}"}





async def html_to_png(url: str):
    """
    将 HTML URL 转换为 PNG（主入口函数）
    从 HTML 中动态提取海报尺寸（如果未指定则默认 1080x1600）
    
    Args:
        url: HTML 文件的网络 URL
    
    Returns:
        {
            "result": "success",
            "file_attachment": "PNG的网络URL"
        }
        或
        {
            "result": "error",
            "message": "错误描述"
        }
    """
    loop = asyncio.get_event_loop()
    
    try:
        png_path = await loop.run_in_executor(_png_executor, _html_to_png_sync, url)
        
        if isinstance(png_path, dict) and png_path.get("result") == "error":
            return png_path
        
        # PNG 生成成功，进行异步上传
        parsed_url = urlparse(url)
        file_name_base = os.path.basename(parsed_url.path) or "poster.html"
        file_name_no_ext = os.path.splitext(file_name_base)[0]
        oss_key = f"user/task_files/{PROJECT_NAME.get()}/{file_name_no_ext}.png"
        updated_png_url = await upload_file_to_oss(png_path, oss_key)
        
        # 清理本地文件
        try:
            if os.path.exists(png_path):
                os.remove(png_path)
        except Exception as e:
            print(f"删除临时PNG文件失败: {str(e)}")
        
        return {
            "result": "success",
            "png_save_path": updated_png_url,
            "file_attachment": updated_png_url
        }
    
    except Exception as e:
        print(f"PNG转换失败: {str(e)}")
        return {"result": "error", "message": str(e)}


def _html_to_png_sync(url: str) -> dict | str:
    """
    同步版本的 HTML→PNG 转换函数
    在线程池中运行，从 HTML 中动态提取海报尺寸（如果未指定则默认 1080x1600）
    
    Args:
        url: HTML 文件的网络 URL
    
    Returns:
        成功返回 PNG 文件路径（字符串），失败返回错误字典
    """
    # 1. 生成临时文件路径
    temp_dir = os.path.join(PROJECT_PATH.get(), "temp")
    os.makedirs(temp_dir, exist_ok=True)
    unique_id = str(uuid.uuid4())
    output_png_path = os.path.join(temp_dir, f"render_{unique_id}.png")
    
    try:
        with sync_playwright() as p:
            # 2. 启动浏览器
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                print(f"Chromium 启动失败: {str(e)}，正在尝试自动安装...")
                import subprocess
                import sys as sys_module
                try:
                    subprocess.run([sys_module.executable, "-m", "playwright", "install", "chromium"], check=True)
                    browser = p.chromium.launch(headless=True)
                except Exception as install_error:
                    return {"result": "error", "message": f"Chromium 安装或启动失败: {str(install_error)}"}
            
            # 3. 获取 HTML 内容并从中提取尺寸
            print(f"正在拉取 HTML 内容: {url}")
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "X-Source-ID": "internal"
                }
                resp = requests.get(url, timeout=30, headers=headers)
                resp.encoding = 'utf-8'
                html_text = resp.text
            except Exception as e:
                return {"result": "error", "message": f"访问HTML页面失败: {str(e)}"}
            
            # 从 HTML 中提取尺寸信息（默认 1080x1600）
            poster_width, poster_height = 1080, 1600
            # 尝试从 @media screen 中的 width/height 提取
            width_match = re.search(r'width:\s*(\d+)px', html_text)
            height_match = re.search(r'height:\s*(\d+)px', html_text)
            if width_match:
                poster_width = int(width_match.group(1))
            if height_match:
                poster_height = int(height_match.group(1))
            
            print(f"📐 检测到海报尺寸: {poster_width}x{poster_height}")
            
            # 4. 配置 Context 和 Page（使用提取到的尺寸）
            with browser.new_context(
                viewport={'width': poster_width, 'height': poster_height},
                device_scale_factor=2,
                accept_downloads=False  # 禁用下载
            ) as context:
                with context.new_page() as page:
                    # 5. 设置页面内容并等待加载
                    page.emulate_media(media="screen")  # 必须在设置内容前设置
                    page.set_content(html_text, wait_until="load", timeout=30000)
                    
                    # 6. 渲染优化：等待网络静默、字体、图片全部加载完成
                    try:
                        page.wait_for_load_state("networkidle", timeout=30000)
                        page.evaluate("() => document.fonts.ready")  # 正确等待字体 Promise
                        # 等待所有图片加载完成
                        page.evaluate("""() => {
                            const imgs = Array.from(document.images);
                            return Promise.all(imgs.map(img => img.complete ? Promise.resolve() : new Promise(resolve => {
                                img.onload = resolve;
                                img.onerror = resolve;
                            })));
                        }""")
                        page.wait_for_timeout(500)  # 额外缓冲，确保渲染完毕
                    except Exception as e:
                        print(f"渲染优化出现非致命错误: {str(e)}")
                    
                    # 7. 生成 PNG（使用动态提取的尺寸）
                    try:
                        # 获取完整的页面截图
                        page.screenshot(
                            path=output_png_path,
                            full_page=False,  # 使用设定的尺寸
                            omit_background=False
                        )
                        return output_png_path
                    except Exception as e:
                        return {"result": "error", "message": f"PNG生成失败: {str(e)}"}
    
    except Exception as e:
        print(f"PNG转换发生意外错误: {str(e)}")
        return {"result": "error", "message": f"PNG转换失败: {str(e)}"}
