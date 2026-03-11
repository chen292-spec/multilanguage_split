import json
import sys
import os

def check_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
            json.loads(content)
        print(f"JSON {path} is valid.")
    except Exception as e:
        print(f"JSON {path} is INVALID: {e}")
        # 尝试查看文件头几个字符确认编码
        with open(path, 'rb') as f:
            print(f"First 20 bytes: {f.read(20)}")

def check_py(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            source = f.read()
            compile(source, path, 'exec')
        print(f"Python {path} is valid.")
    except Exception as e:
        print(f"Python {path} is INVALID: {e}")

root = 'd:/coding/astrbot/AstrBot/data/plugins/multilanguage_split'
check_json(os.path.join(root, '_conf_schema.json'))
check_py(os.path.join(root, 'core/model.py'))
check_py(os.path.join(root, 'core/config.py'))
check_py(os.path.join(root, 'core/step/detect.py'))
check_py(os.path.join(root, 'core/step/send.py'))
