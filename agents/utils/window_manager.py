"""
窗口管理工具模块
用于跨平台（Windows/Linux/Mac）管理浏览器窗口的可见性
"""

import platform
import asyncio
import subprocess
import ctypes


async def hide_chromium_window():
    """
    跨平台隐藏浏览器窗口（Chrome/Chromium）
    
    Windows: 使用ctypes调用Windows API
    Linux: 使用python-xlib操作X11窗口
    """
    await asyncio.sleep(2)  # 等待窗口创建完成
    
    try:
        if platform.system() == 'Windows':
            _hide_window_windows()
        elif platform.system() == 'Linux':
            _hide_window_linux()
        elif platform.system() == 'Darwin':
            print("ℹ️ [Mac] 暂不支持窗口隐藏")
    
    except Exception as e:
        print(f"⚠️ 隐藏窗口异常: {e}")


def _hide_window_windows():
    """Windows平台：隐藏Chromium窗口"""
    try:
        # 可能的Chrome窗口类名（根据版本和启动方式，类名可能不同）
        chrome_classes = [
            "Chrome_WidgetWin_1",
            "Chrome_WidgetWin_0",
            "Chrome_WidgetWin_2",
            "Chromium_WidgetWin_1",
            "Chromium_WidgetWin_0",
            "Chrome_RenderWidgetHostHWND",
        ]
        
        # 尝试用类名查找
        for class_name in chrome_classes:
            hwnd = ctypes.windll.user32.FindWindowW(class_name, None)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
                print(f"✅ 浏览器窗口已隐藏")
                return
        
        # 用FindWindowExW遍历隐藏所有Chrome窗口
        hwnd = ctypes.windll.user32.FindWindowExW(None, None, None, None)
        found = False
        
        while hwnd:
            try:
                classname = ctypes.create_unicode_buffer(256)
                result = ctypes.windll.user32.GetClassNameW(hwnd, classname, 256)
                class_name = classname.value.lower() if result > 0 else ""
                
                if any(x in class_name for x in ['chrome', 'chromium']):
                    ctypes.windll.user32.ShowWindow(hwnd, 0)
                    print(f"✅ 浏览器窗口已隐藏")
                    found = True
                    break
            except:
                pass
            
            hwnd = ctypes.windll.user32.FindWindowExW(None, hwnd, None, None)
        
        if not found:
            print("ℹ️ 未找到浏览器窗口（可能已自动隐藏）")
    
    except Exception as e:
        print(f"⚠️ 隐藏窗口失败: {e}")


def _hide_window_linux():
    """Linux平台：使用python-xlib隐藏X11窗口"""
    try:
        from Xlib import display
        
        d = display.Display()
        root = d.screen().root
        window = _find_window_recursive(root, 'Chromium')
        
        if window:
            window.unmap()  # 隐藏窗口
            d.sync()
            print("✅ 浏览器窗口已隐藏")
        else:
            print("ℹ️ 未找到浏览器窗口")
    
    except ImportError:
        print("⚠️ python-xlib未安装: pip install python-xlib")
    except Exception as e:
        print(f"⚠️ 隐藏窗口失败: {e}")


def _find_window_recursive(window, name_fragment):
    """
    递归查找窗口（支持Chrome/Chromium）
    
    Args:
        window: 起始窗口对象
        name_fragment: 窗口名称片段
    
    Returns:
        找到的窗口对象，或None
    """
    try:
        window_name = window.get_wm_name()
        if window_name and (name_fragment.lower() in window_name.lower() or 
                           'chrome' in window_name.lower()):
            return window
    except:
        pass
    
    try:
        for child in window.query_tree().children:
            result = _find_window_recursive(child, name_fragment)
            if result:
                return result
    except:
        pass
    
    return None


async def show_chromium_window():
    """跨平台显示浏览器窗口（恢复隐藏的窗口）"""
    try:
        if platform.system() == 'Windows':
            _show_window_windows()
        elif platform.system() == 'Linux':
            _show_window_linux()
    except Exception as e:
        print(f"⚠️ 显示窗口异常: {e}")


async def kill_chromium_process():
    """
    杀死隐藏的浏览器进程（Chrome/Chromium）
    
    当窗口隐藏时，taskkill /IM 可能无效，需要用其他方式
    """
    try:
        if platform.system() == 'Windows':
            _kill_chromium_windows()
        elif platform.system() == 'Linux':
            _kill_chromium_linux()
        else:
            print("⚠️ 暂不支持此平台的进程杀死")
    except Exception as e:
        print(f"⚠️ 杀死进程失败: {e}")


def _kill_chromium_windows():
    """Windows：杀死隐藏的Chromium进程（相比taskkill更可靠）"""
    try:
        # 试图杀死的进程名列表（可能是chromium或chrome）
        process_names = ['chromium.exe', 'chrome.exe']
        killed = False
        
        for proc_name in process_names:
            try:
                # 方式1：用wmic（最可靠，能杀死隐藏进程）
                result = subprocess.run(
                    ['wmic', 'process', 'where', f'name="{proc_name}"', 'delete'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                
                if result.returncode == 0 and '删除' in result.stdout or '删除' in result.stderr:
                    print(f"✅ {proc_name} 进程已杀死 (wmic)")
                    killed = True
                    break
            except:
                pass
        
        if not killed:
            # 方式2：用PowerShell Stop-Process（尝试两种进程名）
            result = subprocess.run(
                ['powershell', '-Command', 
                 'Stop-Process -Name chromium -Force -ErrorAction SilentlyContinue; '
                 'Stop-Process -Name chrome -Force -ErrorAction SilentlyContinue'],
                capture_output=True,
                text=True,
                timeout=5
            )
            print("✅ 浏览器进程已杀死 (PowerShell)")
    
    except Exception as e:
        print(f"⚠️ 杀死Windows进程失败: {e}")


def _kill_chromium_linux():
    """Linux：杀死隐藏的Chromium进程"""
    try:
        # 尝试杀死chromium和chrome两种可能
        subprocess.run(['pkill', '-9', 'chromium'], timeout=5)
        subprocess.run(['pkill', '-9', 'chrome'], timeout=5)
        
        print("✅ 浏览器进程已杀死 (pkill)")
    
    except Exception as e:
        print(f"⚠️ 杀死Linux进程失败: {e}")


def _show_window_windows():
    """Windows平台：显示隐藏的浏览器窗口"""
    try:
        # 尝试多个可能的窗口类名
        chrome_classes = [
            "Chrome_WidgetWin_1",
            "Chrome_WidgetWin_0",
            "Chromium_WidgetWin_1",
            "Chromium_WidgetWin_0",
        ]
        
        for class_name in chrome_classes:
            hwnd = ctypes.windll.user32.FindWindowW(class_name, None)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 1)  # SW_SHOW
                print("✅ 浏览器窗口已显示")
                return
        
        print("ℹ️ 未找到浏览器窗口")
    except Exception as e:
        print(f"⚠️ 显示窗口失败: {e}")


def _show_window_linux():
    """Linux平台：显示隐藏的X11窗口"""
    try:
        from Xlib import display
        
        d = display.Display()
        root = d.screen().root
        window = _find_window_recursive(root, 'Chromium')
        
        if window:
            window.map()  # 显示窗口
            d.sync()
            print("✅ 浏览器窗口已显示")
    except ImportError:
        print("⚠️ python-xlib未安装")
    except Exception as e:
        print(f"⚠️ 显示窗口失败: {e}")
