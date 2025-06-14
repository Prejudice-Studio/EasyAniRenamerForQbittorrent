import os
import re
import shutil
import sys
import configparser
from pathlib import Path
from qbittorrentapi import Client, LoginFailed

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

CONFIG = {
    'VIDEO_EXTS': ('.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv'),
    'SUBS_EXTS': ('.ass', '.srt', '.ssa', '.sub', '.idx'),
    'CONFIG_FILE': os.path.join(os.path.dirname(os.path.abspath(__file__)), 'qb_renamer_config.ini'),
    'DEFAULT_EPISODE_REGEXES': [
        r"\[(\d{2})\][^\\/]*$",
        r"\b(\d{2})\b",
        r"E(\d{2})",
        r"第(\d{2})话",
        r"EP?(\d{2})",
        r"- (\d{2}) -",
        r"_(\d{2})_",
        r" (\d{2}) "
    ],
    'DEFAULT_MAX_DIR_DEPTH': '1'
}

class QBitRenamer:
    def __init__(self, debug=None):
        self.debug = False
        self._init_console_encoding()
        self.config = configparser.ConfigParser()
        self._init_config()
        self.load_config()
        
        if not self._check_first_run():
            self.setup_credentials()
        
        self.debug = debug if debug is not None else self.config.getboolean('SETTINGS', 'debug_mode', fallback=False)
        self._print_debug("🛠️ 初始化完成", force=True)
        self.client = None
        self.episode_regexes = self._init_episode_regexes()
        self.lang_map = self._init_lang_map()
        
    def _init_episode_regexes(self):
        """初始化集数正则表达式列表（带有效性验证）"""
        default_regexes = [
            r"S\d+E(\d+)",                  # 匹配 S01E01 格式
            r"\[\s*(\d{2})\s*\]",            # 匹配 [01] 格式
            r"\bEP?\s*(\d{2})\b",            # 匹配 EP01 或 E01
            r"第\s*(\d{2})\s*[话集]",        # 匹配 第01话
            r"\s(\d{2})(?=\D*\.mkv)",        # 匹配空格后的两位数字（在扩展名前）
            r"_(\d{2})_",                    # 匹配 _01_
            r"- (\d{2}) -"                   # 匹配 - 01 -
        ]
        if self.config.has_option('SETTINGS', 'episode_regexes'):
            raw = self.config.get('SETTINGS', 'episode_regexes')
            regexes = []
            for idx, pattern in enumerate(raw.split('\n'), 1):
                pattern = pattern.strip()
                if not pattern:
                    continue
                try:
                    re.compile(pattern)
                    regexes.append(pattern)
                except re.error as e:
                    print(f"⚠️ 忽略无效正则表达式 #{idx}: {pattern} ({e})")
            if regexes:
                return regexes
        return CONFIG['DEFAULT_EPISODE_REGEXES']

    def _check_first_run(self):
        required_keys = ['host', 'username', 'password']
        for key in required_keys:
            if not self.config['QBITTORRENT'].get(key):
                print("\n🔐 首次使用需要设置qBittorrent WebUI凭据")
                return False
        return True

    def _init_console_encoding(self):
        try:
            if sys.platform == 'win32':
                import _locale
                _locale._gdl_bak = _locale._getdefaultlocale
                _locale._getdefaultlocale = lambda *args: ('en_US', 'utf-8')
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except Exception as e:
            print(f"⚠️ 无法设置控制台编码: {e}")

    def _init_config(self):
        self.config['QBITTORRENT'] = {
            ';host': 'qBittorrent WebUI访问地址',
            'host': 'localhost:8080',
            ';username': 'WebUI登录用户名',
            'username': 'admin',
            ';password': 'WebUI登录密码',
            'password': 'adminadmin',
            ';default_tag': '默认处理的种子标签',
            'default_tag': 'anime',
            ';processed_tag': '处理完成的种子标签',
            'processed_tag': 'processed'
        }
        self.config['SETTINGS'] = {
            ';default_mode': '操作模式: direct(直接重命名) | copy(复制) | move(移动) | pre(试运行)',
            'default_mode': 'direct',
            ';workspace': '文件输出目录 (仅copy/move模式需要)',
            'workspace': str(Path.home() / 'Anime_Renamed'),
            ';auto_tag_processed': '处理后自动添加processed标签 (true/false)',
            'auto_tag_processed': 'true',
            ';skip_processed': '跳过已处理标签的种子 (true/false)',
            'skip_processed': 'true',
            ';dry_run_first': '首次运行默认试运行模式 (true/false)',
            'dry_run_first': 'true',
            ';debug_mode': '显示详细调试信息 (true/false)',
            'debug_mode': 'false',
            ';episode_regexes': '集数匹配正则表达式列表（每行一个，按顺序尝试）',
            'episode_regexes': '\n'.join([
                r'\[(\d{2})\][^\\/]*$',
                r'\b(\d{2})\b',
                r'E(\d{2})',
                r'第(\d{2})话',
                r'EP?(\d{2})',
                r'- (\d{2}) -',
                r'_(\d{2})_',
                r' (\d{2}) '
            ]),
            ';scan_subdirs': '扫描子目录中的文件 (true/false)',
            'scan_subdirs': 'true',
            ';subgroup_mode': '是否启用字幕组标记功能 (true/false)',
            'subgroup_mode': 'false',
            ';max_dir_depth': '最大子目录扫描深度 (默认为1)',
            'max_dir_depth': CONFIG['DEFAULT_MAX_DIR_DEPTH'],
            ';excluded_dirs': '要跳过的文件夹列表(逗号分隔,不区分大小写)',
            'excluded_dirs': 'SPs,CDs,Scans'
        }
        self.config['NAMING'] = {
            ';season_format': '季集格式 (可用变量: {season}-季号, {episode}-集号)',
            'season_format': 'S{season}E{episode}',
            ';video_prefix': '视频文件前缀标记',
            'video_prefix': '[Video]',
            ';sub_prefix': '字幕文件前缀标记', 
            'sub_prefix': '[Subtitle]',
            ';language_format': '语言标识格式 (可用变量: {lang})',
            'language_format': '.{lang}',
            ';custom_format': '文件名格式 (可用变量: {prefix} {season_ep} {custom} {lang} {ext})',
            'custom_format': '{prefix} {season_ep}{custom}{lang}{ext}'
        }
        self.config['LANGUAGE'] = {
            '; 语言检测规则说明': '格式: 匹配模式 = 语言标识',
            '\\.chs&jap\\.': 'CHS&JP',
            '\\.cht&jap\\.': 'CHT&JP',
            '\\.jpsc\\.': 'JP&CHS', 
            '\\.jptc\\.': 'JP&CHT',
            '\\.sc\\.': 'CHS',
            '\\.chs\\.': 'CHS',
            '\\[简\\]': 'CHS',
            '\\.tc\\.': 'CHT',
            '\\.cht\\.': 'CHT',
            '\\[繁\\]': 'CHT',
            '\\.jap\\.': 'JP',
            '\\.jp\\.': 'JP',
            '\\.jpn\\.': 'JP',
            '\\[日\\]': 'JP',
            '\\.eng\\.': 'EN',
            '\\.en\\.': 'EN',
            '\\[英\\]': 'EN'
        }

    def load_config(self):
        try:
            if os.path.exists(CONFIG['CONFIG_FILE']):
                self.config = configparser.ConfigParser(
                    interpolation=None,
                    allow_no_value=True,
                    delimiters=('=',),
                    inline_comment_prefixes=(';',)
                )
            
                # 自定义读取器处理续行符
                with open(CONFIG['CONFIG_FILE'], 'r', encoding='utf-8') as f:
                    content = []
                    continuation = False
                    for line in f:
                        line = line.rstrip('\n')
                        if line.endswith('\\'):
                            content.append(line.rstrip('\\').strip())
                            continuation = True
                        else:
                            if continuation:
                                content[-1] += ' ' + line.strip()
                            else:
                                content.append(line)
                            continuation = False
                    self.config.read_string('\n'.join(content))
            
                # 处理多行正则表达式
                if self.config.has_option('SETTINGS', 'episode_regexes'):
                    raw = self.config.get('SETTINGS', 'episode_regexes')
                    self.config['SETTINGS']['episode_regexes'] = '\n'.join(
                        [line.strip() for line in raw.splitlines() if line.strip()]
                    )
            else:
                self._print_debug("🆕 创建默认配置", force=True)
                self.save_config()
        except Exception as e:
            self._print_debug(f"❌ 配置读取错误: {e}", force=True)
            self._backup_config()
            self._init_config()

    def _backup_config(self):
        backup_path = CONFIG['CONFIG_FILE'] + '.bak'
        try:
            if os.path.exists(CONFIG['CONFIG_FILE']):
                shutil.copyfile(CONFIG['CONFIG_FILE'], backup_path)
                print(f"⚠️ 配置已损坏，已备份到: {backup_path}")
        except Exception as e:
            print(f"❌ 无法备份配置文件: {e}")

    def save_config(self):
        try:
            with open(CONFIG['CONFIG_FILE'], 'w', encoding='utf-8') as f:
                f.write("# 自动生成的配置文件\n")
                f.write("# 以分号(;)开头的行是配置说明，程序会自动忽略\n\n")
                for section in self.config.sections():
                    f.write(f"[{section}]\n")
                    for k, v in self.config[section].items():
                        if k.startswith(';'):
                            f.write(f"; {v}\n")
                        else:
                            # 修正正则表达式保存方式
                            if section == 'SETTINGS' and k == 'episode_regexes':
                                f.write(f"{k} = \n")
                                for line in v.split('\n'):
                                    f.write(f"    {line}\n")
                            else:
                                f.write(f"{k} = {v}\n")
                    f.write("\n")
                self._print_debug(f"💾 配置已保存到: {CONFIG['CONFIG_FILE']}")
        except Exception as e:
            print(f"❌ 配置保存失败: {e}")

    def show_config(self):
        print("\n📋 当前配置说明:")
        section_helps = {
            'QBITTORRENT': 'qBittorrent连接设置',
            'SETTINGS': '程序行为设置',
            'NAMING': '文件名格式设置',
            'LANGUAGE': '语言检测规则'
        }
        for section in self.config.sections():
            print(f"\n[{section}] {section_helps.get(section, '')}")
            for key in [k for k in self.config[section] if not k.startswith(';')]:
                value = self.config[section][key]
                help_text = self.config[section].get(f';{key}', '')
                print(f"  {key:20} = {value}")
                if help_text:
                    print(f"    {help_text}")
        
        # 特别显示排除目录设置
        if 'SETTINGS' in self.config and 'excluded_dirs' in self.config['SETTINGS']:
            print("\n🔍 当前排除目录设置:")
            excluded = self.config['SETTINGS']['excluded_dirs'].split(',')
            print(" , ".join([d.strip() for d in excluded if d.strip()]))

    def _edit_section(self, section):
        print(f"\n编辑 [{section}] 配置")
        print("="*60)
        
        # 显示当前配置
        for key in [k for k in self.config[section] if not k.startswith(';')]:
            value = self.config[section][key]
            help_text = self.config[section].get(f';{key}', '')
            print(f"{key:20} = {value}")
            if help_text:
                print(f"  {help_text}")
        
        # 特殊处理SETTINGS节的排除目录
        if section == 'SETTINGS':
            print("\n🛑 排除目录设置")
            print("-"*40)
            current_excluded = self.config[section].get('excluded_dirs', 'SPs,CDs,Scans')
            excluded_list = [d.strip() for d in current_excluded.split(',') if d.strip()]
            print("当前排除的目录: " + ", ".join(excluded_list) if excluded_list else "无")
            
            while True:
                action = input("\n操作: [a]添加 [d]删除 [c]清除 [s]设置新列表 [回车继续]: ").lower().strip()
                if not action:
                    break
                    
                if action == 'a':  # 添加
                    to_add = input("输入要添加的目录名(多个用逗号分隔): ").strip()
                    if to_add:
                        current = set(excluded_list)
                        current.update([d.strip() for d in to_add.split(',') if d.strip()])
                        excluded_list = sorted(current)
                        print("更新后列表:", ", ".join(excluded_list))
                        
                elif action == 'd':  # 删除
                    if not excluded_list:
                        print("⚠️ 当前没有可删除的目录")
                        continue
                    print("当前排除目录:", ", ".join(f"[{i}] {d}" for i, d in enumerate(excluded_list)))
                    try:
                        to_remove = input("输入要删除的编号或名称(多个用空格分隔): ").strip()
                        if to_remove:
                            indices = set()
                            names = set()
                            for item in to_remove.split():
                                if item.isdigit():
                                    idx = int(item)
                                    if 0 <= idx < len(excluded_list):
                                        indices.add(idx)
                                else:
                                    names.add(item.lower())
                            
                            # 保留不在删除列表中的项目
                            new_list = [
                                d for i, d in enumerate(excluded_list)
                                if i not in indices and d.lower() not in names
                            ]
                            if len(new_list) != len(excluded_list):
                                excluded_list = new_list
                                print("更新后列表:", ", ".join(excluded_list) if excluded_list else "空")
                    except Exception as e:
                        print(f"⚠️ 输入错误: {e}")
                        
                elif action == 'c':  # 清除
                    if input("确认清除所有排除目录? (y/n): ").lower() == 'y':
                        excluded_list = []
                        print("已清除所有排除目录")
                        
                elif action == 's':  # 设置新列表
                    new_list = input("输入新的排除目录列表(逗号分隔): ").strip()
                    if new_list:
                        excluded_list = [d.strip() for d in new_list.split(',') if d.strip()]
                        print("更新后列表:", ", ".join(excluded_list) if excluded_list else "空")
                        
            # 保存修改后的排除目录列表
            if excluded_list:
                self.config[section]['excluded_dirs'] = ", ".join(excluded_list)
            else:
                self.config[section]['excluded_dirs'] = ""
        
        # 语言表特殊编辑界面
        if section == 'LANGUAGE':
            print("\n🛠️ 语言表编辑模式 (输入格式: 模式 原内容=新内容)")
            print("模式: replace(替换)/delete(删除)/add(添加)")
            print("示例:")
            print("  replace \\.chs\\.=CHS → 替换现有规则")
            print("  delete \\.chs\\.=CHS → 删除规则")
            print("  add \\.french\\.=FR → 添加新规则")
            
            while True:
                try:
                    edit_cmd = input("\n输入编辑命令 (留空结束): ").strip()
                    if not edit_cmd:
                        break
                        
                    parts = edit_cmd.split(maxsplit=1)
                    if len(parts) < 2:
                        print("⚠️ 格式错误，需要包含模式和内容")
                        continue
                        
                    mode = parts[0].lower()
                    content = parts[1]
                    
                    if mode not in ('replace', 'delete', 'add'):
                        print("⚠️ 无效模式，请使用replace/delete/add")
                        continue
                        
                    if '=' not in content:
                        print("⚠️ 需要包含等号(=)分隔键值")
                        continue
                        
                    key, value = content.split('=', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    if mode == 'delete':
                        if key not in self.config[section] or self.config[section][key] != value:
                            print("⚠️ 规则不存在或不匹配")
                            continue
                            
                        print(f"将删除: {key} = {value}")
                        if input("确认删除? (y/n): ").lower() == 'y':
                            del self.config[section][key]
                            print("✅ 已删除")
                            
                    elif mode == 'add':
                        if not (key.startswith('\\') or key.startswith('[')):
                            print("⚠️ 键应以\\.或\\[开头")
                            continue
                            
                        if key in self.config[section]:
                            print("⚠️ 键已存在")
                            continue
                            
                        print(f"将添加: {key} = {value}")
                        if input("确认添加? (y/n): ").lower() == 'y':
                            self.config[section][key] = value
                            print("✅ 已添加")
                            
                    elif mode == 'replace':
                        if key not in self.config[section]:
                            print("⚠️ 原规则不存在")
                            continue
                            
                        print(f"将替换: {key} = {self.config[section][key]} → {value}")
                        if input("确认替换? (y/n): ").lower() == 'y':
                            self.config[section][key] = value
                            print("✅ 已替换")
                            
                except Exception as e:
                    print(f"❌ 处理出错: {e}")
                    if self.debug:
                        import traceback
                        traceback.print_exc()
                    continue
        
        # 常规配置项编辑
        while True:
            key = input("\n输入要修改的键名 (留空结束编辑): ").strip()
            if not key:
                break
                
            if key not in self.config[section] or key.startswith(';'):
                print("⚠️ 无效键名")
                continue
                
            # 跳过已特殊处理的键
            if (section == 'SETTINGS' and key == 'excluded_dirs') or \
            (section == 'LANGUAGE' and not key.startswith(';')):
                continue
                
            current_value = self.config[section][key]
            
            # 处理多行值（如正则表达式列表）
            if key == 'episode_regexes' and section == 'SETTINGS':
                print(f"\n当前 {key} 值 (多行):")
                print("-"*40)
                print(current_value)
                print("-"*40)
                print("输入新的正则表达式列表（每行一个，空行结束）:")
                lines = []
                while True:
                    line = input(f"正则 {len(lines)+1}: ").strip()
                    if not line:
                        break
                    try:
                        re.compile(line)  # 验证正则表达式
                        lines.append(line)
                    except re.error as e:
                        print(f"⚠️ 无效正则表达式: {e}")
                        
                if lines:
                    new_value = '\n'.join(lines)
                    print(f"\n新值预览:")
                    print("-"*40)
                    print(new_value)
                    print("-"*40)
                    if input("确认更新? (y/n): ").lower() == 'y':
                        self.config[section][key] = new_value
                        print("✅ 已更新")
                continue
                
            # 处理布尔值
            if current_value.lower() in ('true', 'false'):
                new_value = input(f"切换 {key} 值 (当前: {current_value}) [y/n]: ").lower()
                new_value = 'true' if new_value == 'y' else 'false'
            else:
                new_value = input(f"输入 {key} 的新值 (当前: {current_value}): ").strip()
                
            if new_value:
                self.config[section][key] = new_value
                print(f"✅ 已更新 {key} = {new_value}")
        
        save = input("\n是否保存更改? (y/n): ").lower() == 'y'
        if save:
            self.save_config()
            print("✅ 配置已保存")
        else:
            print("⏹️ 更改已丢弃")

    def edit_config(self):
        print("\n⚙️ 配置编辑器")
        print("="*60)
        sections = list(self.config.sections())
        for i, section in enumerate(sections, 1):
            print(f"{i}. {section}")
        while True:
            try:
                choice = input("\n选择要编辑的配置部分 (1-{}，q退出): ".format(len(sections)))
                if choice.lower() == 'q':
                    break
                section_idx = int(choice) - 1
                if 0 <= section_idx < len(sections):
                    section = sections[section_idx]
                    self._edit_section(section)
                else:
                    print("⚠️ 无效选择")
            except ValueError:
                print("⚠️ 请输入数字或q退出")

    def _print_debug(self, message, force=False):
        if self.debug or force:
            print(f"🐛 [DEBUG] {message}")

    def _confirm_continue(self, prompt):
        if self.debug:
            choice = input(f"{prompt} (y/n): ").lower()
            return choice == 'y'
        return True

    def _init_lang_map(self):
        """初始化语言映射表（修复大小写不敏感）"""
        lang_map = {}
        if 'LANGUAGE' in self.config:
            for key, value in self.config['LANGUAGE'].items():
                if not key.startswith(';'):
                    # 将配置中的模式转换为忽略大小写的正则表达式
                    pattern = key.replace('\\.', '.').replace('\\[', '[').replace('\\]', ']')
                    lang_map[pattern] = value
        return lang_map

    def connect_qbittorrent(self):
        self._print_debug("🔌 尝试连接qBittorrent")
        if not self._confirm_continue("继续连接qBittorrent?"):
            return False
        if not self.config['QBITTORRENT']['username']:
            self.setup_credentials()
        try:
            self.client = Client(
                host=self.config['QBITTORRENT']['host'],
                username=self.config['QBITTORRENT']['username'],
                password=self.config['QBITTORRENT']['password']
            )
            self.client.auth_log_in()
            self._print_debug("✅ 连接成功")
            return True
        except Exception as e:
            print(f"❌ 连接失败: {e}")
            return False

    def setup_credentials(self):
        """设置qBittorrent连接凭据"""
        print("\n⚙️ 首次运行配置向导")
        print("="*60)
        
        # 显示当前配置
        print("\n📋 当前qBittorrent配置:")
        print(f"🌐 WebUI地址: {self.config['QBITTORRENT'].get('host', '未设置')}")
        print(f"👤 用户名: {self.config['QBITTORRENT'].get('username', '未设置')}")
        print(f"🔑 密码: {'*' * len(self.config['QBITTORRENT'].get('password', '')) if self.config['QBITTORRENT'].get('password') else '未设置'}")
        
        # 获取用户输入
        print("\n🛠️ 请输入以下信息:")
        self.config['QBITTORRENT']['host'] = input("🌐 WebUI地址 (默认localhost:8080): ") or 'localhost:8080'
        self.config['QBITTORRENT']['username'] = input("👤 用户名: ").strip()
        self.config['QBITTORRENT']['password'] = input("🔑 密码: ").strip()
        
        # 保存配置
        self.save_config()
        print("\n✅ 配置已保存！")

    def detect_language(self, filename):
        """最终修正的语言检测方法"""
        try:
            filename = str(filename).lower()  # 统一转为小写
            self._print_debug(f"🔍 检测语言 - 文件名: {filename}")

            # 按优先级从高到低检查的语言规则
            LANGUAGE_RULES = [
                (r'\.chs&jap\.', 'CHS&JP'),
                (r'\.cht&jap\.', 'CHT&JP'),
                (r'\.jpsc\.', 'JP&CHS'),
                (r'\.jptc\.', 'JP&CHT'),
                (r'\.sc\.', 'CHS'),      # 必须放在.chs.前面
                (r'\.chs\.', 'CHS'),     # 必须放在.cht.前面
                (r'\[简\]', 'CHS'),
                (r'\.tc\.', 'CHT'),      # 必须放在.cht.前面
                (r'\.cht\.', 'CHT'),     # 必须放在.chs.后面
                (r'\[繁\]', 'CHT'),
                (r'\.jap\.', 'JP'),
                (r'\.jp\.', 'JP'),
                (r'\.jpn\.', 'JP'),
                (r'\[日\]', 'JP'),
                (r'\.eng\.', 'EN'),
                (r'\.en\.', 'EN'),
                (r'\[英\]', 'EN')
            ]

            for pattern, lang in LANGUAGE_RULES:
                # 使用re.IGNORECASE确保大小写不敏感
                if re.search(pattern, filename, re.IGNORECASE):
                    self._print_debug(f"✅ 匹配成功: {pattern} → {lang}")
                    return lang

            self._print_debug("⚠️ 未匹配到任何语言规则")
            return None
        except Exception as e:
            self._print_debug(f"❌ 语言检测出错: {e}")
            return None
        
    def detect_episode(self, filename):
        """使用配置的正则列表检测集号"""
        for idx, pattern in enumerate(self.episode_regexes, 1):
            try:
                if match := re.search(pattern, filename, re.IGNORECASE):
                    self._print_debug(f"✅ 正则 #{idx} 匹配成功: {pattern} → {match.group(1)}")
                    return match.group(1)
            except re.error as e:
                self._print_debug(f"⚠️ 无效正则 #{idx}: {pattern} ({e})")
        return None

    def _sanitize_filename(self, filename):
        illegal_chars = r'[\\/*?:"<>|]'
        return re.sub(illegal_chars, '', filename)

    def generate_new_name(self, file_path, prefix, season, custom_str, is_video, subgroup_tag=""):
        try:
            file_path = Path(file_path)
            filename = file_path.name
            self._print_debug(f"\n📝 开始处理文件: {filename}")

            if not (episode := self.detect_episode(filename)):
                self._print_debug("❌ 无法提取集号")
                return None
            
            # 添加字幕组标记
            if subgroup_tag:
                prefix = f"[{subgroup_tag}] {prefix}"  # 添加方括号包裹

            lang_str = ""
            if not is_video:
                if lang := self.detect_language(filename):
                    lang_str = f".{lang}"
                    self._print_debug(f"🔠 语言标签: {lang_str}")

            season_str = str(season).zfill(2)
            episode_str = str(episode).zfill(2)
            
            clean_custom = f".{self._sanitize_filename(custom_str)}" if custom_str else ""
            
            new_name = (
                f"{prefix} "
                f"S{season_str}E{episode_str}"
                f"{clean_custom}"
                f"{lang_str}"
                f"{file_path.suffix}"
            )

            new_name = (
                new_name.replace("..", ".")
                .replace(" .", ".")
                .replace(". ", ".")
                .strip()
            )

            self._print_debug(f"✅ 最终文件名: {new_name}")
            return new_name
        except Exception as e:
            self._print_debug(f"❌ 生成文件名出错: {e}")
            return None

    def select_mode(self):
        modes = [
            {'id': 'direct', 'name': '直接模式', 'desc': '直接通过qBittorrent API重命名文件', 'warning': '⚠️ 直接修改qBittorrent中的文件（高风险）', 'emoji': '⚡'},
            {'id': 'copy', 'name': '复制模式', 'desc': '复制文件到工作目录并重命名', 'warning': '✅ 安全模式，不影响原文件', 'emoji': '📋'},
            {'id': 'move', 'name': '移动模式', 'desc': '移动文件到工作目录并重命名', 'warning': '⚠️ 原文件将被移动到新位置', 'emoji': '🚚'},
            {'id': 'pre', 'name': '试运行模式', 'desc': '仅预览重命名效果，不实际操作', 'warning': '✅ 安全模式，仅显示结果', 'emoji': '👀'}
        ]
        print("\n🔧 请选择操作模式:")
        for i, mode in enumerate(modes, 1):
            print(f"{i}. {mode['emoji']} {mode['name']}")
            print(f"   {mode['desc']}")
            print(f"   {mode['warning']}\n")
        
        default_mode = self.config['SETTINGS']['default_mode']
        if self.config['SETTINGS'].getboolean('dry_run_first'):
            default_mode = 'pre'
        
        while True:
            choice = input(f"选择模式 (1-{len(modes)}, 默认 {default_mode}): ").strip().lower()
            if not choice:
                choice = default_mode
                break
            elif choice.isdigit() and 1 <= int(choice) <= len(modes):
                choice = modes[int(choice)-1]['id']
                break
            elif choice in [m['id'] for m in modes]:
                break
            print("⚠️ 无效选择，请重新输入")
        
        self.config['SETTINGS']['default_mode'] = choice
        self.save_config()
        return choice

    def _display_file_tree(self, files, max_depth=1):
        """显示文件目录树结构（最终修正版）
        
        参数:
            files: 文件列表，每个元素是包含'name'和'progress'的字典
            max_depth: 最大显示深度
        """
        file_tree = {}
        
        # 收集所有唯一路径
        path_items = set()
        for f in files:
            if f.get('progress', 0) >= 1:  # 只处理完成的文件
                path = Path(f['name'])
                parts = path.parts[:max_depth + 1]  # 限制深度
                path_items.add(tuple(parts))  # 使用元组保证可哈希
        
        # 构建树形结构
        for parts in sorted(path_items):
            current_level = file_tree
            for i, part in enumerate(parts):
                if i == len(parts) - 1 and i >= max_depth:
                    # 文件层级
                    if 'files' not in current_level:
                        current_level['files'] = []
                    current_level['files'].append(part)
                else:
                    # 目录层级
                    if part not in current_level:
                        current_level[part] = {}
                    current_level = current_level[part]
        
        def _print_tree(node, prefix='', is_last=True):
            """递归打印树结构"""
            # 打印目录
            dirs = [(k, v) for k, v in node.items() if k != 'files']
            for i, (name, child) in enumerate(dirs):
                last = i == len(dirs) - 1 and 'files' not in node
                print(f"{prefix}{'└── ' if last else '├── '}{name}")
                _print_tree(child, f"{prefix}{'    ' if last else '│   '}", last)
            
            # 打印文件
            if 'files' in node:
                files = node['files']
                for i, name in enumerate(files):
                    print(f"{prefix}{'└── ' if i == len(files)-1 else '├── '}{name}")
        
        print(f"\n📂 文件目录结构预览 (最大深度: {max_depth}):")
        print(".")  # 根目录
        _print_tree(file_tree)

    def _process_directory(self, base_path, current_path, files, mode, workspace, 
                        prefix, season, custom_str, subgroup_tag, dir_depth=1):
        """处理单个目录中的文件（跳过排除目录）"""
        operations = []
        file_tree = {}
        
        current_path = Path(current_path)
        base_path = Path(base_path)
        
        # 获取排除目录列表
        excluded_dirs = {d.strip().lower() for d in 
                        self.config['SETTINGS'].get('excluded_dirs', 'SPs,CDs,Scans').split(',') 
                        if d.strip()}
        
        # 检查当前目录是否在排除列表中
        if current_path.name.lower() in excluded_dirs:
            self._print_debug(f"⏭️ 跳过排除目录: {current_path}")
            return operations, file_tree
            
        for file in files:
            # 跳过未完成文件
            if file.get('progress', 0) < 1:
                continue
                
            file_path = Path(file['name'])
            
            # 精确匹配当前目录
            try:
                if file_path.parent != current_path:
                    continue
            except ValueError:
                continue
                
            # 检查文件类型
            ext = file_path.suffix.lower()
            is_video = ext in CONFIG['VIDEO_EXTS']
            is_sub = ext in CONFIG['SUBS_EXTS']
            if not (is_video or is_sub):
                continue
                
            # 生成新文件名
            new_name = self.generate_new_name(
                file_path, prefix, season, custom_str, is_video, subgroup_tag
            )
            if not new_name:
                continue
                
            # 确定操作类型
            if mode == 'copy':
                dest = Path(workspace) / new_name
                operations.append(('copy', str(file_path), str(dest)))
            elif mode == 'move':
                dest = Path(workspace) / new_name
                operations.append(('move', str(file_path), str(dest)))
            elif mode == 'direct':
                dest = str(file_path.parent / new_name)
                operations.append(('rename', str(file_path), dest))
            else:  # preview
                operations.append(('preview', str(file_path), str(file_path.parent / new_name)))
            
            # 记录文件信息
            file_tree[file_path.name] = {
                'type': 'video' if is_video else 'sub',
                'new_name': new_name,
                'original_path': str(file_path),
                'subgroup': subgroup_tag
            }
        
        return operations, file_tree

    def process_torrents(self):
        self._print_debug("🚀 开始处理种子")
        if not self._confirm_continue("开始处理种子?"):
            return

        # 硬编码忽略的关键词列表（不区分大小写）
        IGNORED_KEYWORDS = {'oad', 'ova', 'sp', 'special', 'ncop', 'nced', 'pv'}
        
        # 获取标签设置
        default_tag = self.config['QBITTORRENT'].get('default_tag', '')
        tag = input(f"\n🏷️ 要处理的标签 (默认 '{default_tag}', 留空退出): ").strip() or default_tag
        if not tag:
            self._print_debug("⏹️ 用户退出")
            return
        
        # 初始化正则表达式
        self.episode_regexes = self._init_episode_regexes()
        self._print_debug(f"📌 使用正则模式列表: {self.episode_regexes}")

        # 字幕组标记设置
        subgroup_enabled = self.config.getboolean('SETTINGS', 'subgroup_mode', fallback=False)
        subgroup_choice = input("\n是否启用字幕组标记? (y/n, 默认{}): ".format("是" if subgroup_enabled else "否")).lower()
        subgroup_enabled = subgroup_choice in ('y', 'yes') if subgroup_choice else subgroup_enabled
        self.config['SETTINGS']['subgroup_mode'] = 'true' if subgroup_enabled else 'false'

        # 目录深度设置
        try:
            max_depth = int(self.config['SETTINGS'].get('max_dir_depth', CONFIG['DEFAULT_MAX_DIR_DEPTH']))
        except (ValueError, KeyError):
            max_depth = int(CONFIG['DEFAULT_MAX_DIR_DEPTH'])
            self.config['SETTINGS']['max_dir_depth'] = str(max_depth)

        if input(f"\n📂 当前最大目录扫描深度为 {max_depth}，是否修改？(y/n): ").lower() == 'y':
            while True:
                try:
                    new_depth = int(input("请输入新的最大扫描深度 (1-5，推荐1-2): "))
                    if 1 <= new_depth <= 5:
                        max_depth = new_depth
                        self.config['SETTINGS']['max_dir_depth'] = str(new_depth)
                        self.save_config()
                        print(f"✅ 已更新最大目录扫描深度为 {new_depth}")
                        break
                    print("⚠️ 请输入1-5之间的数字")
                except ValueError:
                    print("⚠️ 请输入有效的数字")

        # 操作模式选择
        mode = self.select_mode()
        workspace = None

        if mode in ('copy', 'move'):
            while True:
                workspace = input(f"📁 输入工作目录 (必须指定): ").strip()
                if workspace:
                    workspace = Path(workspace)
                    try:
                        workspace.mkdir(parents=True, exist_ok=True)
                        break
                    except Exception as e:
                        print(f"❌ 无法创建工作目录: {e}")
                        if input("是否重试? (y/n): ").lower() != 'y':
                            return
                else:
                    print("⚠️ 工作目录不能为空")

        # 获取排除目录设置
        excluded_dirs = {d.strip().lower() for d in 
                        self.config['SETTINGS'].get('excluded_dirs', 'SPs,CDs,Scans').split(',') 
                        if d.strip()}
        self._print_debug(f"🚫 排除目录列表: {excluded_dirs}")
        self._print_debug(f"🚫 忽略文件关键词: {IGNORED_KEYWORDS}")

        # 连接qBittorrent获取种子
        self._print_debug(f"🔍 扫描标签: {tag}")
        try:
            torrents = self.client.torrents_info(tag=tag)
        except Exception as e:
            print(f"❌ 获取种子列表失败: {e}")
            if hasattr(e, 'response'):
                print(f"HTTP 错误详情: {e.response.text}")
            return

        # 跳过已处理种子
        if self.config['SETTINGS'].getboolean('skip_processed'):
            processed_tag = self.config['QBITTORRENT'].get('processed_tag', 'processed')
            torrents = [t for t in torrents if processed_tag not in t.tags.split(',')]

        if not torrents:
            print("⚠️ 没有找到可处理的种子")
            return

        all_operations = []
        for torrent in torrents:
            print(f"\n🎬 发现种子: {torrent.name}")
            print(f"📂 保存路径: {torrent.save_path}")
        
            try:
                files = self.client.torrents_files(torrent.hash)
                print(f"📦 文件数量: {len(files)}")
                self._display_file_tree(files, max_depth)
            except Exception as e:
                print(f"⚠️ 无法获取文件列表: {e}")
                if input("是否继续处理下一个种子? (y/n): ").lower() != 'y':
                    break
                continue
        
            if input("\n是否处理此种子? (y/n, 默认y): ").lower() not in ('', 'y', 'yes'):
                continue
            
            # 收集所有需要处理的深层目录（depth > 0）
            base_path = Path(files[0].name).parent if files else Path('.')
            deep_dirs = {}
            
            # 扫描所有深层目录（depth > 0且不超过max_depth），排除特定文件夹
            for f in files:
                try:
                    f_path = Path(f.name)
                    current_depth = len(f_path.parts) - len(base_path.parts)
                    if 1 <= current_depth <= max_depth:  # 只处理深度>0的目录
                        dir_path = f_path.parent
                        dir_name = dir_path.name.lower()
                        
                        # 检查是否在排除列表中
                        if dir_name in excluded_dirs:
                            self._print_debug(f"⏭️ 跳过排除目录: {dir_path}")
                            continue
                            
                        if dir_path not in deep_dirs:
                            deep_dirs[dir_path] = {
                                'files': [],
                                'needs_custom': False
                            }
                        deep_dirs[dir_path]['files'].append(f)
                except Exception as e:
                    self._print_debug(f"⚠️ 处理文件路径出错: {f.name} → {e}")
                    continue

            # 如果没有深层目录，检查是否有根目录文件需要处理
            root_files = []
            if not deep_dirs:
                for f in files:
                    try:
                        f_path = Path(f.name)
                        if len(f_path.parts) - len(base_path.parts) == 0:  # 根目录文件
                            root_files.append(f)
                    except Exception as e:
                        self._print_debug(f"⚠️ 处理文件路径出错: {f.name} → {e}")
                        continue

            # 第一阶段：参数设置（仅当有深层目录时才跳过根目录设置）
            current_subgroup = ""
            if subgroup_enabled:
                while True:
                    current_subgroup = input(f"为此种子输入字幕组标记 (留空则不添加): ").strip()
                    if not current_subgroup or (len(current_subgroup) <= 20 and not any(c in r'\/:*?"<>|' for c in current_subgroup)):
                        break
                    print("⚠️ 字幕组标记不能包含特殊字符且长度不超过20")

            suggested_prefix = torrent.category or re.sub(r'[\[\]_]', ' ', torrent.name).strip()[:30]
            while True:
                prefix = input(f"📌 输入前缀 (建议: {suggested_prefix}, 留空使用建议): ").strip() or suggested_prefix
                if len(prefix) <= 50:
                    break
                print("⚠️ 前缀长度不能超过50字符")

            while True:
                default_season = input(f"  输入默认季号 (默认01): ").strip().zfill(2) or '01'
                if default_season.isdigit() and 1 <= int(default_season) <= 99:
                    break
                print("⚠️ 请输入01-99之间的数字")

            custom_str = input("✍️ 自定义标识 (如WEB-DL, 可选): ").strip()[:20]

            # 第二阶段：处理深层目录（完全独立设置，不继承任何参数）
            processed_operations = []
            if deep_dirs:
                print("\n🔍 发现深层目录，将单独设置每个目录参数")
                for dir_path in sorted(deep_dirs.keys(), key=lambda x: str(x)):
                    print(f"\n📁 正在设置目录: {dir_path}")
                    
                    # 每个深层目录都单独设置参数
                    dir_subgroup = current_subgroup
                    if subgroup_enabled:
                        while True:
                            dir_subgroup = input(f"为此目录输入字幕组标记 (留空则不添加): ").strip()
                            if not dir_subgroup or (len(dir_subgroup) <= 20 and not any(c in r'\/:*?"<>|' for c in dir_subgroup)):
                                break
                            print("⚠️ 字幕组标记不能包含特殊字符且长度不超过20")

                    while True:
                        dir_prefix = input(f"输入此前缀 (建议: {suggested_prefix}): ").strip() or suggested_prefix
                        if len(dir_prefix) <= 50:
                            break
                        print("⚠️ 前缀长度不能超过50字符")

                    while True:
                        dir_season = input(f"输入此季号 (默认01): ").strip().zfill(2) or '01'
                        if dir_season.isdigit() and 1 <= int(dir_season) <= 99:
                            break
                        print("⚠️ 请输入01-99之间的数字")

                    dir_custom = input("✍️ 自定义标识 (如WEB-DL, 可选): ").strip()[:20]

                    # 处理目录文件
                    operations = []
                    file_tree = {}
                    
                    for file in deep_dirs[dir_path]['files']:
                        file_path = Path(file['name'])
                        filename_lower = file_path.name.lower()
                        
                        # 检查是否包含忽略关键词
                        if any(keyword in filename_lower for keyword in IGNORED_KEYWORDS):
                            self._print_debug(f"⏭️ 跳过含忽略关键词的文件: {file_path.name}")
                            continue
                            
                        # 检查文件类型
                        ext = file_path.suffix.lower()
                        is_video = ext in CONFIG['VIDEO_EXTS']
                        is_sub = ext in CONFIG['SUBS_EXTS']
                        if not (is_video or is_sub):
                            continue
                            
                        # 生成新文件名
                        new_name = self.generate_new_name(
                            file_path, dir_prefix, dir_season, 
                            dir_custom, is_video, dir_subgroup
                        )
                        if not new_name:
                            continue
                            
                        # 确定操作类型
                        if mode == 'copy':
                            dest = Path(workspace) / new_name
                            operations.append(('copy', str(file_path), str(dest)))
                        elif mode == 'move':
                            dest = Path(workspace) / new_name
                            operations.append(('move', str(file_path), str(dest)))
                        elif mode == 'direct':
                            dest = str(file_path.parent / new_name)
                            operations.append(('rename', str(file_path), dest))
                        else:  # preview
                            operations.append(('preview', str(file_path), str(file_path.parent / new_name)))
                        
                        # 记录文件信息
                        file_tree[file_path.name] = {
                            'type': 'video' if is_video else 'sub',
                            'new_name': new_name,
                            'original_path': str(file_path),
                            'subgroup': dir_subgroup
                        }
                    
                    if operations:
                        print(f"\n🔍 目录 {dir_path} 重命名预览:")
                        for filename, info in sorted(file_tree.items()):
                            print(f"{'🎬' if info['type'] == 'video' else '📝'} {filename} → {info['new_name']}")
                        
                        if input("\n确认处理此目录? (y/n): ").lower() == 'y':
                            processed_operations.extend(operations)
            
            # 第三阶段：处理根目录文件（仅当没有深层目录时）
            elif root_files:
                print("\n🔍 未发现深层目录，处理根目录文件")
                operations = []
                file_tree = {}
                
                for file in root_files:
                    file_path = Path(file['name'])
                    filename_lower = file_path.name.lower()
                    
                    # 检查是否包含忽略关键词
                    if any(keyword in filename_lower for keyword in IGNORED_KEYWORDS):
                        self._print_debug(f"⏭️ 跳过含忽略关键词的文件: {file_path.name}")
                        continue
                        
                    # 检查文件类型
                    ext = file_path.suffix.lower()
                    is_video = ext in CONFIG['VIDEO_EXTS']
                    is_sub = ext in CONFIG['SUBS_EXTS']
                    if not (is_video or is_sub):
                        continue
                        
                    # 生成新文件名
                    new_name = self.generate_new_name(
                        file_path, prefix, default_season, 
                        custom_str, is_video, current_subgroup
                    )
                    if not new_name:
                        continue
                        
                    # 确定操作类型
                    if mode == 'copy':
                        dest = Path(workspace) / new_name
                        operations.append(('copy', str(file_path), str(dest)))
                    elif mode == 'move':
                        dest = Path(workspace) / new_name
                        operations.append(('move', str(file_path), str(dest)))
                    elif mode == 'direct':
                        dest = str(file_path.parent / new_name)
                        operations.append(('rename', str(file_path), dest))
                    else:  # preview
                        operations.append(('preview', str(file_path), str(file_path.parent / new_name)))
                    
                    # 记录文件信息
                    file_tree[file_path.name] = {
                        'type': 'video' if is_video else 'sub',
                        'new_name': new_name,
                        'original_path': str(file_path),
                        'subgroup': current_subgroup
                    }
                
                if operations:
                    print(f"\n🔍 根目录重命名预览:")
                    for filename, info in sorted(file_tree.items()):
                        print(f"{'🎬' if info['type'] == 'video' else '📝'} {filename} → {info['new_name']}")
                    
                    if input("\n确认处理根目录文件? (y/n): ").lower() == 'y':
                        processed_operations.extend(operations)

            if processed_operations:
                all_operations.append({
                    'hash': torrent.hash,
                    'name': torrent.name,
                    'operations': processed_operations,
                    'params': {
                        'prefix': prefix,
                        'season': default_season,
                        'custom': custom_str,
                        'subgroup': current_subgroup
                    }
                })

        if not all_operations:
            print("⚠️ 没有生成任何操作")
            return
        
        self.show_full_preview(all_operations, mode, subgroup_enabled)

        if mode != 'pre' and input("\n⚠️ 确认执行以上操作? (y/n): ").lower() == 'y':
            total_success = 0
            for torrent in all_operations:
                print(f"\n🔄 处理: {torrent['name']}")
                success = 0
                
                for op_type, src, dst in torrent['operations']:
                    try:
                        if op_type == 'copy':
                            shutil.copy2(src, dst)
                        elif op_type == 'move':
                            shutil.move(src, dst)
                        elif op_type == 'rename':
                            self.client.torrents_rename_file(
                                torrent_hash=torrent['hash'],
                                old_path=src,
                                new_path=Path(src).parent / Path(dst).name
                            )
                        success += 1
                        self._print_debug(f"✅ 成功: {src} → {dst}")
                    except Exception as e:
                        print(f"❌ 操作失败 {src} → {e}")
                        if self.debug:
                            import traceback
                            traceback.print_exc()
                
                if success > 0 and self.config['SETTINGS'].getboolean('auto_tag_processed'):
                    old_tag = self.config['QBITTORRENT'].get('default_tag', '').strip()
                    new_tag = self.config['QBITTORRENT'].get('processed_tag', 'processed').strip()
                    
                    try:
                        current_tags = self.client.torrents_info(torrent_hashes=torrent['hash'])[0].tags.split(', ')
                        
                        if old_tag and old_tag in current_tags:
                            self.client.torrents_remove_tags(torrent_hashes=torrent['hash'], tags=[old_tag])
                        
                        if new_tag not in current_tags:
                            self.client.torrents_add_tags(torrent_hashes=torrent['hash'], tags=[new_tag])
                            
                        print(f"🏷️ 标签更新: 移除 {old_tag} → 添加 {new_tag}")
                        
                        updated = self.client.torrents_info(torrent_hashes=torrent['hash'])[0]
                        print(f"🔍 当前标签: {updated.tags}")
                        
                    except Exception as e:
                        print(f"⚠️ 标签更新失败: {str(e)}")
                        if hasattr(e, 'response'):
                            print(f"HTTP 错误详情: {e.response.text}")

                total_success += success
                print(f"✅ 完成: {success}/{len(torrent['operations'])}")

            print(f"\n🎉 全部完成! 成功处理 {total_success} 个文件")
        else:
            print("⏹️ 操作已取消")
        
    def show_full_preview(self, all_operations, mode, subgroup_enabled=False):
        mode_names = {
            'direct': '⚡ 直接模式',
            'copy': '📋 复制模式',
            'move': '🚚 移动模式',
            'pre': '👀 试运行模式'
        }
        
        print(f"\n🔎 完整操作预览 ({mode_names.get(mode, mode)})")
        print("="*80)
        print(f"🔍 使用的集数匹配正则: {self.episode_regexes}")
        if subgroup_enabled:
            print(f"🔖 字幕组标记功能已启用")
        print("="*80)
        
        total_stats = {
            'torrents': len(all_operations),
            'videos': 0,
            'subs': 0,
            'total': 0,
            'dirs': 0
        }
        
        for torrent in all_operations:
            print(f"\n📌 种子: {torrent['name']}")
            print(f"├─ 📂 路径: {torrent.get('path', '根目录')}")
            # 修正参数访问路径
            print(f"├─ 🔤 前缀: {torrent['params']['prefix']}")  # 正确访问方式
            print(f"├─ 🏷️ 季号: S{torrent['params']['season']}")
            if subgroup_enabled and torrent['params'].get('subgroup'):
                print(f"├─ 🔖 字幕组: {torrent['params']['subgroup']}")
            if torrent['params'].get('custom'):
                print(f"├─ ✍️ 自定义标识: {torrent['params']['custom']}")
            
            stats = {'videos': 0, 'subs': 0}
            for op in torrent['operations']:
                ext = Path(op[1]).suffix.lower()
                if ext in CONFIG['VIDEO_EXTS']:
                    stats['videos'] += 1
                elif ext in CONFIG['SUBS_EXTS']:
                    stats['subs'] += 1
            
            total_stats['videos'] += stats['videos']
            total_stats['subs'] += stats['subs']
            total_stats['total'] += stats['videos'] + stats['subs']
            total_stats['dirs'] += 1
            
            print(f"├─ 🎬 视频: {stats['videos']} | 📝 字幕: {stats['subs']} | 📦 总计: {stats['videos'] + stats['subs']}")
            print(f"└─ 🔧 操作类型: {mode_names.get(mode, mode)}")

        print("\n📊 全局统计:")
        print(f"• 🏷️ 总种子数: {total_stats['torrents']}")
        print(f"• 📂 总目录数: {total_stats['dirs']}")
        print(f"• 🎬 总视频文件: {total_stats['videos']}")
        print(f"• 📝 总字幕文件: {total_stats['subs']}")
        print(f"• 📦 总文件数: {total_stats['total']}")
        print("="*80)

    def run(self):
        print("\n🎬 qBittorrent文件整理工具 v12.8")
        print(f"📝 配置文件: {CONFIG['CONFIG_FILE']}")
        print("="*60)
        
        # 显示当前配置
        print("\n📋 当前主要配置:")
        print(f"🌐 WebUI地址: {self.config['QBITTORRENT'].get('host', '未设置')}")
        print(f"👤 用户名: {self.config['QBITTORRENT'].get('username', '未设置')}")
        print(f"🔑 密码: {'*' * len(self.config['QBITTORRENT'].get('password', '')) if self.config['QBITTORRENT'].get('password') else '未设置'}")
        
        config_action = input("\n是否查看/编辑当前配置? (v查看/e编辑/回车跳过): ").lower()
        if config_action == 'v':
            self.show_config()
        elif config_action == 'e':
            self.edit_config()
        
        if not self.connect_qbittorrent():
            return
            
        try:
            while True:
                self.process_torrents()
                if not self._confirm_continue("\n是否继续处理其他标签?"):
                    break
        except KeyboardInterrupt:
            print("\n🛑 用户中断操作")
        except Exception as e:
            print(f"❌ 发生错误: {e}")
            if self.debug:
                import traceback
                traceback.print_exc()
        finally:
            if self.client:
                try:
                    self.client.auth_log_out()
                except:
                    pass
            print("\n✅ 程序退出")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='🎬 qBittorrent文件整理工具')
    parser.add_argument('--debug', action='store_true', help='🐛 启用调试模式')
    parser.add_argument('--config', help='📂 指定配置文件路径')
    args = parser.parse_args()
    
    if args.config:
        CONFIG['CONFIG_FILE'] = args.config
    
    try:
        QBitRenamer(debug=args.debug).run()
    except ImportError as e:
        print(f"❌ 需要安装依赖: pip install qbittorrent-api\n{e}")