import os
import yaml
import sqlite3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH")

# 1. 动态获取路径
# __file__ 是当前 Dante.py 的路径
# .parent 是 internal 目录
# .parent.parent 是 OpenLOA 根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MODEL_CONFIG_PATH = BASE_DIR / "configs" / "models.yaml"
TOOL_CONFIG_PATH = BASE_DIR / "configs" / "tools.yaml"

# 2. 配置缓存（支持自动刷新）
_model_config_cache = {}
_model_config_mtime = None
_tool_config_cache = {}
_tool_config_mtime = None


def _should_reload_model_config():
    """检查 models.yaml 是否被修改过"""
    global _model_config_mtime
    try:
        current_mtime = os.path.getmtime(MODEL_CONFIG_PATH)
        if _model_config_mtime is None or current_mtime != _model_config_mtime:
            _model_config_mtime = current_mtime
            return True
    except OSError:
        pass
    return False


def _should_reload_tool_config():
    """检查 tools.yaml 是否被修改过"""
    global _tool_config_mtime
    try:
        current_mtime = os.path.getmtime(TOOL_CONFIG_PATH)
        if _tool_config_mtime is None or current_mtime != _tool_config_mtime:
            _tool_config_mtime = current_mtime
            return True
    except OSError:
        pass
    return False


def load_model_config(config_name):
    """
    加载模型配置，支持自动热更新
    - 首次加载：从磁盘读取并缓存
    - 文件未改动：从缓存返回（零开销）
    - 文件已改动：重新读取并刷新缓存
    """
    global _model_config_cache
    
    # 检查缓存是否失效
    if _should_reload_model_config() or config_name not in _model_config_cache:
        try:
            with open(MODEL_CONFIG_PATH, "r", encoding="utf-8") as f:
                all_configs = yaml.safe_load(f)
                _model_config_cache = all_configs or {}
                print(f"✅ 已加载模型配置: {MODEL_CONFIG_PATH}")
        except FileNotFoundError:
            print(f"❌ 错误：找不到配置文件 {MODEL_CONFIG_PATH}")
            _model_config_cache = {}
        except Exception as e:
            print(f"❌ 读取配置出错: {e}")
            _model_config_cache = {}
    
    return _model_config_cache.get(config_name, {})


def load_tool_config(tool_name):
    """
    加载工具配置，支持自动热更新
    - 首次加载：从磁盘读取并缓存
    - 文件未改动：从缓存返回（零开销）
    - 文件已改动：重新读取并刷新缓存
    """
    global _tool_config_cache
    
    # 检查缓存是否失效
    if _should_reload_tool_config() or tool_name not in _tool_config_cache:
        try:
            with open(TOOL_CONFIG_PATH, "r", encoding="utf-8") as f:
                all_configs = yaml.safe_load(f)
                _tool_config_cache = all_configs or {}
                print(f"✅ 已加载工具配置: {TOOL_CONFIG_PATH}")
        except FileNotFoundError:
            print(f"❌ 错误：找不到配置文件 {TOOL_CONFIG_PATH}")
            _tool_config_cache = {}
        except Exception as e:
            print(f"❌ 读取配置出错: {e}")
            _tool_config_cache = {}
    
    return _tool_config_cache.get(tool_name, {})


# 从数据库读取用户设置（如语言偏好等）
def load_user_settings(user_id):
    # 初始化默认配置
    settings = {"language": "en"}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            # 设置 row_factory 允许通过列名访问结果
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # 这里可以预先查询多个字段，为以后扩展做准备
            query = "SELECT language_pref FROM user_info WHERE user_id = ?"
            cursor.execute(query, (user_id,))
            row = cursor.fetchone()
            if row and row["language_pref"]:
                settings["language"] = row["language_pref"]
                # 以后如果要加新字段，只需在这里赋值：
                # settings["theme"] = row["theme_pref"]
    except sqlite3.Error as e:
        # 在生产环境中建议使用 logging 模块记录错误
        print(f"Database error: {e}")
    return settings