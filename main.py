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
    'DEFAULT_EPISODE_REGEX': r"\[(\d{2})(?:v\d+)?\]",
    'DEFAULT_MAX_DIR_DEPTH': '1'
}

class QBitRenamer:
    def __init__(self, debug=None):
        self.debug = False
        self._init_console_encoding()
        self.config = configparser.ConfigParser()
        self._init_config()
        self.load_config()
        
        # 添加首次运行检查
        if not self._check_first_run():
            self.setup_credentials()
        
        self.debug = debug if debug is not None else self.config.getboolean('SETTINGS', 'debug_mode', fallback=False)
        self._print_debug("🛠️ 初始化完成", force=True)
        self.client = None
        self.episode_regex = self.config.get('SETTINGS', 'default_episode_regex', fallback=CONFIG['DEFAULT_EPISODE_REGEX'])
        self.lang_map = self._init_lang_map()

    def _check_first_run(self):
        """检查是否是首次运行"""
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
            'default_tag': 'anime'
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
            ';default_episode_regex': '集数匹配正则表达式',
            'default_episode_regex': CONFIG['DEFAULT_EPISODE_REGEX'],
            ';scan_subdirs': '扫描子目录中的文件 (true/false)',
            'scan_subdirs': 'true',
            ';subgroup_mode': '是否启用字幕组标记功能 (true/false)',
            'subgroup_mode': 'false',
            ';max_dir_depth': '最大子目录扫描深度 (默认为1)',
            'max_dir_depth': CONFIG['DEFAULT_MAX_DIR_DEPTH']
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
                with open(CONFIG['CONFIG_FILE'], 'r', encoding='utf-8') as f:
                    lines = [line for line in f if not line.strip().startswith(';')]
                self.config = configparser.ConfigParser()
                self.config.read_string('\n'.join(lines))
                if not self.config['QBITTORRENT'].get('host'):
                    self.config['QBITTORRENT']['host'] = 'localhost:8080'
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

    def _edit_section(self, section):
        print(f"\n编辑 [{section}] 配置")
        print("="*60)
        for key in [k for k in self.config[section] if not k.startswith(';')]:
            value = self.config[section][key]
            help_text = self.config[section].get(f';{key}', '')
            print(f"{key:20} = {value}")
            if help_text:
                print(f"  {help_text}")
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
                    parts = edit_cmd.split()
                    if len(parts) < 2:
                        print("⚠️ 格式错误，需要包含模式和内容")
                        continue
                    mode = parts[0].lower()
                    content = ' '.join(parts[1:])
                    if mode not in ('replace', 'delete', 'add'):
                        print("⚠️ 无效模式，请使用replace/delete/add")
                        continue
                    if mode == 'delete':
                        if '=' not in content:
                            print("⚠️ 删除模式需要格式: key=value")
                            continue
                        key, value = content.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if key not in self.config[section] or self.config[section][key] != value:
                            print("⚠️ 规则不存在或不匹配")
                            continue
                        print(f"将删除: {key} = {value}")
                        if input("确认删除? (y/n): ").lower() == 'y':
                            del self.config[section][key]
                            print("✅ 已删除")
                    elif mode == 'add':
                        if '=' not in content:
                            print("⚠️ 添加模式需要格式: key=value")
                            continue
                        key, value = content.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        if not (key.startswith('\\') or key.startswith('[')):
                            print("⚠️ 键应以\\.或\\[开头")
                            continue
                        print(f"将添加: {key} = {value}")
                        if input("确认添加? (y/n): ").lower() == 'y':
                            self.config[section][key] = value
                            print("✅ 已添加")
                    elif mode == 'replace':
                        if '=' not in content:
                            print("⚠️ 替换模式需要格式: old_key=new_value")
                            continue
                        parts = [p.strip() for p in content.split('=') if p.strip()]
                        if len(parts) != 2:
                            print("⚠️ 替换模式需要格式: old_key=new_value")
                            continue
                        old_key, new_value = parts
                        if old_key not in self.config[section]:
                            print("⚠️ 原规则不存在")
                            continue
                        if not new_value:
                            print(f"将删除: {old_key} = {self.config[section][old_key]}")
                            if input("确认删除? (y/n): ").lower() == 'y':
                                del self.config[section][old_key]
                                print("✅ 已删除")
                        else:
                            print(f"将替换: {old_key} = {self.config[section][old_key]} → {new_value}")
                            if input("确认替换? (y/n): ").lower() == 'y':
                                self.config[section][old_key] = new_value
                                print("✅ 已替换")
                    if input("\n继续修改? (y/n): ").lower() != 'y':
                        break
                except Exception as e:
                    print(f"❌ 处理出错: {e}")
                    continue
        else:
            while True:
                key = input("\n输入要修改的键名 (留空结束编辑): ").strip()
                if not key:
                    break
                if key not in self.config[section] or key.startswith(';'):
                    print("⚠️ 无效键名")
                    continue
                new_value = input(f"输入 {key} 的新值 (当前: {self.config[section][key]}): ").strip()
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
        lang_map = {}
        if 'LANGUAGE' in self.config:
            for key, value in self.config['LANGUAGE'].items():
                if not key.startswith(';'):
                    pattern = key.replace('\\.', '.')
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
        self._print_debug(f"🔍 检测语言标识: {filename}")
        filename = filename.lower()
        for pattern, lang in self.lang_map.items():
            if re.search(pattern, filename):
                self._print_debug(f"✅ 检测到语言: {lang} (模式: {pattern})")
                return lang
        self._print_debug("⚠️ 未检测到语言标识")
        return None

    def _sanitize_filename(self, filename):
        illegal_chars = r'[\\/*?:"<>|]'
        return re.sub(illegal_chars, '', filename)

    def generate_new_name(self, file_path, prefix, season, custom_str, is_video, subgroup_tag=""):
        self._print_debug(f"📝 开始处理: {file_path.name}")
        episode_match = re.search(self.episode_regex, file_path.name)
        if not episode_match:
            self._print_debug(f"❌ 集号匹配失败，使用正则: {self.episode_regex}")
            return None

        episode = episode_match.group(1)
        version = episode_match.group(2) if len(episode_match.groups()) > 1 else ''
        season_str = str(season).zfill(2)
        episode_str = str(episode).zfill(2) + version

        lang_str = ''
        if not is_video:
            detected_lang = self.detect_language(file_path.name)
            if detected_lang:
                lang_str = f".{detected_lang.strip('.')}"
                self._print_debug(f"🔍 检测到语言标签: {lang_str}")

        if subgroup_tag:
            prefix = f"[{subgroup_tag}] {prefix.strip()}"
            self._print_debug(f"🏷️ 添加字幕组标记: {subgroup_tag}")

        custom_part = ''
        if custom_str:
            cleaned_custom = self._sanitize_filename(custom_str.strip())
            custom_part = f".{cleaned_custom}" if cleaned_custom else ''

        title_part = f"{prefix.strip()} S{season_str}E{episode_str}"
        detail_part = f"{custom_part}{lang_str}"
        new_name = f"{title_part}{detail_part}{file_path.suffix}"

        new_name = re.sub(r'\.{2,}', '.', new_name)
        new_name = re.sub(r'(?<!S\d{2}E\d{2})\.', '.', new_name, count=1)
        new_name = re.sub(r'\s+', ' ', new_name)
        new_name = new_name.replace(' .', '.').replace('. ', '.')

        if not re.match(r'^.* S\d{2}E\d{2}\..+', new_name):
            self._print_debug("⚠️ 格式校验失败，正在尝试修复...")
            new_name = re.sub(r'(S\d{2}E\d{2})', r'\1.', new_name, count=1)

        self._print_debug(f"✅ 最终文件名: {new_name}")
        return new_name

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
        """显示文件目录树结构"""
        file_tree = {}
        for file in files:
            path = Path(file['name'])
            parts = path.parts
            current_level = file_tree
            
            for i, part in enumerate(parts[:max_depth]):
                if part not in current_level:
                    current_level[part] = {}
                current_level = current_level[part]
        
        def _print_tree(node, prefix='', is_last=True):
            connector = '└── ' if is_last else '├── '
            print(prefix + connector + node_name)
            new_prefix = prefix + ('    ' if is_last else '│   ')
            items = list(node.items())
            for i, (child_name, child_node) in enumerate(items):
                _print_tree(child_node, new_prefix, i == len(items)-1)
        
        print("\n📂 文件目录结构预览 (最大深度: {}):".format(max_depth))
        print(".")
        for i, (node_name, node) in enumerate(file_tree.items()):
            _print_tree(node, '', i == len(file_tree)-1)

    def _process_directory(self, base_path, current_path, files, mode, workspace, prefix, season, custom_str, subgroup_tag, dir_depth=1):
        """处理单个目录中的文件"""
        operations = []
        file_tree = {}
        
        for file in files:
            file_path = Path(file['name'])
            relative_path = file_path.relative_to(base_path)
            
            if len(relative_path.parts) > dir_depth + 1:
                continue
                
            ext = file_path.suffix.lower()
            is_video = ext in CONFIG['VIDEO_EXTS']
            is_sub = ext in CONFIG['SUBS_EXTS']
            
            if not (is_video or is_sub) or file['progress'] < 1:
                continue
                
            new_name = self.generate_new_name(
                file_path, prefix, season, custom_str, is_video,
                subgroup_tag=subgroup_tag
            )
            if not new_name:
                continue
                
            if mode == 'copy':
                dest = workspace / new_name
                operations.append(('copy', str(file_path), str(dest)))
            elif mode == 'move':
                dest = workspace / new_name
                operations.append(('move', str(file_path), str(dest)))
            elif mode == 'direct':
                dest = str(file_path.parent / new_name) if len(file_path.parts) > 1 else new_name
                operations.append(('rename', str(file_path), dest))
            else:
                operations.append(('preview', str(file_path), str(file_path.parent / new_name)))
            
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
        
        default_tag = self.config['QBITTORRENT'].get('default_tag', '')
        tag = input(f"\n🏷️ 要处理的标签 (默认 '{default_tag}', 留空退出): ").strip() or default_tag
        if not tag:
            self._print_debug("⏹️ 用户退出")
            return
            
        custom_regex = input(f"🔍 输入自定义集数匹配正则 (留空使用默认 '{CONFIG['DEFAULT_EPISODE_REGEX']}'): ").strip()
        self.episode_regex = custom_regex if custom_regex else CONFIG['DEFAULT_EPISODE_REGEX']
        self._print_debug(f"📌 使用正则模式: {self.episode_regex}")
        
        subgroup_enabled = self.config.getboolean('SETTINGS', 'subgroup_mode', fallback=False)
        if input("\n是否启用字幕组标记? (y/n, 默认{}): ".format("是" if subgroup_enabled else "否")).lower() in ('y', 'yes'):
            subgroup_enabled = True
            self.config['SETTINGS']['subgroup_mode'] = 'true'
        else:
            subgroup_enabled = False
            self.config['SETTINGS']['subgroup_mode'] = 'false'
        self.save_config()
        
        # 获取和设置最大目录深度
        try:
            max_depth = int(self.config['SETTINGS'].get('max_dir_depth', CONFIG['DEFAULT_MAX_DIR_DEPTH']))
        except (ValueError, KeyError):
            max_depth = int(CONFIG['DEFAULT_MAX_DIR_DEPTH'])
        
        change_depth = input(f"\n📂 当前最大目录扫描深度为 {max_depth}，是否修改？(y/n): ").lower()
        if change_depth == 'y':
            while True:
                try:
                    new_depth = int(input("请输入新的最大扫描深度 (1-5，推荐1-2): "))
                    if 1 <= new_depth <= 5:
                        max_depth = new_depth
                        self.config['SETTINGS']['max_dir_depth'] = str(new_depth)
                        self.save_config()
                        print(f"✅ 已更新最大目录扫描深度为 {new_depth}")
                        break
                    else:
                        print("⚠️ 请输入1-5之间的数字")
                except ValueError:
                    print("⚠️ 请输入有效的数字")

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
                else:
                    print("⚠️ 工作目录不能为空")
            self._print_debug(f"📂 工作目录: {workspace}")
        
        self._print_debug(f"🔍 扫描标签: {tag}")
        torrents = self.client.torrents_info(tag=tag)
        
        if self.config['SETTINGS'].getboolean('skip_processed'):
            torrents = [t for t in torrents if 'processed' not in t['tags'].split(',')]
        
        if not torrents:
            print("⚠️ 没有找到可处理的种子")
            return
        
        all_operations = []
        for torrent in torrents:
            print(f"\n🎬 发现种子: {torrent['name']}")
            print(f"📂 保存路径: {torrent['save_path']}")
            
            try:
                files = self.client.torrents_files(torrent['hash'])
                print(f"📦 文件数量: {len(files)}")
                
                # 显示文件目录结构
                self._display_file_tree(files, max_depth)
            except Exception as e:
                print(f"⚠️ 无法获取文件列表: {e}")
                continue
            
            if input("\n是否处理此种子? (y/n, 默认y): ").lower() not in ('', 'y', 'yes'):
                self._print_debug(f"⏭️ 用户跳过种子: {torrent['name']}")
                continue
                
            current_subgroup = ""
            if subgroup_enabled:
                current_subgroup = input(f"为此种子输入字幕组标记 (留空则不添加): ").strip().upper()
            
            suggested_prefix = torrent.get('category', '').strip() or re.sub(r'[\[\]_]', ' ', torrent['name']).strip()
            suggested_prefix = re.sub(r'\s+', ' ', suggested_prefix)[:30]
            
            prefix = input(f"📌 输入前缀 (建议: {suggested_prefix}, 留空使用建议): ").strip()
            if not prefix:
                prefix = suggested_prefix
                print(f"使用建议前缀: {prefix}")
            
            season = input(f"  输入季号 (默认01): ").strip().zfill(2) or '01'
            custom_str = input("✍️ 自定义标识 (如WEB-DL, 可选): ").strip()
            
            self._print_debug(f"🔤 前缀: {prefix}, 季号: {season}, 字幕组: {current_subgroup}, 自定义: {custom_str}")
            
            files = self.client.torrents_files(torrent['hash'])
            base_path = Path(files[0]['name']).parent if len(files) > 0 else Path('.')
            
            dirs_to_process = {base_path: {'prefix': prefix, 'season': season, 'custom': custom_str, 'subgroup': current_subgroup}}
            processed_dirs = set()
            
            while dirs_to_process:
                current_dir, params = dirs_to_process.popitem()
                processed_dirs.add(current_dir)
                
                dir_files = [f for f in files if Path(f['name']).parent == current_dir]
                if not dir_files:
                    continue
                    
                operations, file_tree = self._process_directory(
                    base_path, current_dir, dir_files, mode, workspace,
                    params['prefix'], params['season'], params['custom'], 
                    params['subgroup'], dir_depth=max_depth
                )
                
                if operations:
                    print(f"\n🔍 目录 {current_dir} 重命名预览:")
                    print("="*60)
                    for filename, info in file_tree.items():
                        file_type = "🎬" if info['type'] == 'video' else "📝"
                        print(f"{file_type} {filename}")
                        print(f"→ {info['new_name']}")
                        print("-"*60)
                    
                    if input("\n确认处理此目录? (y/n): ").lower() == 'y':
                        all_operations.append({
                            'name': torrent['name'],
                            'hash': torrent['hash'],
                            'prefix': params['prefix'],
                            'season': params['season'],
                            'subgroup': params['subgroup'],
                            'custom': params['custom'],
                            'operations': operations,
                            'file_tree': file_tree,
                            'path': str(current_dir)
                        })
                        self._print_debug(f"✅ 为目录 {current_dir} 生成 {len(operations)} 个操作")
                    else:
                        self._print_debug(f"⏭️ 用户取消处理目录: {current_dir}")
                
                if len(processed_dirs) < max_depth:
                    subdirs = {Path(f['name']).parent for f in files 
                              if len(Path(f['name']).parts) > len(current_dir.parts) + 1 
                              and Path(f['name']).parent not in processed_dirs}
                    
                    for subdir in subdirs:
                        if input(f"\n发现子目录 {subdir}, 要单独处理吗? (y/n): ").lower() == 'y':
                            sub_prefix = input(f"输入此目录的前缀 (默认继承 {params['prefix']}): ").strip() or params['prefix']
                            sub_season = input(f"输入此目录的季号 (默认 {params['season']}): ").strip() or params['season']
                            sub_custom = input(f"输入此目录的自定义标识 (默认 {params['custom']}): ").strip() or params['custom']
                            sub_subgroup = input(f"输入此目录的字幕组标记 (默认 {params['subgroup']}): ").strip() or params['subgroup']
                            dirs_to_process[subdir] = {
                                'prefix': sub_prefix,
                                'season': sub_season,
                                'custom': sub_custom,
                                'subgroup': sub_subgroup
                            }
        
        if not all_operations:
            print("⚠️ 没有生成任何操作，可能原因：")
            print("- 没有找到符合条件的文件（视频/字幕）")
            print("- 文件进度未完成")
            print("- 集数正则不匹配文件名")
            print("- 文件在子目录中但配置了不扫描子目录")
            return
            
        self.show_full_preview(all_operations, mode, subgroup_enabled)
        
        if mode != 'pre':
            confirm = input("\n⚠️ 确认执行以上操作? (y/n): ").lower()
            if confirm != 'y':
                print("⏹️ 操作已取消")
                return
                
            total_success = 0
            total_files = sum(len(t['operations']) for t in all_operations)
            
            for torrent in all_operations:
                print(f"\n🔄 正在处理: {torrent['name']} ({torrent['path']})")
                success = 0
                
                for op_type, src, dst in torrent['operations']:
                    try:
                        self._print_debug(f"⚡ 执行: {op_type} {src} → {dst}")
                        
                        if not self._confirm_continue(f"确认 {op_type} {src} → {dst}?"):
                            continue
                            
                        if op_type == 'copy':
                            Path(dst).parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src, dst)
                        elif op_type == 'move':
                            Path(dst).parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(src, dst)
                        else:
                            self.client.torrents_rename_file(
                                torrent_hash=torrent['hash'],
                                old_path=src,
                                new_path=dst
                            )
                        
                        success += 1
                    except Exception as e:
                        print(f"❌ 失败: {src} → {e}")
                
                print(f"✅ 完成: {success}/{len(torrent['operations'])}")
                total_success += success
                
                if self.config['SETTINGS'].getboolean('auto_tag_processed'):
                    self.client.torrents_add_tags(torrent['hash'], 'processed')
            
            print(f"\n🎉 全部完成! 成功: {total_success}/{total_files}")
        
        print("\n" + "="*60)

    def show_full_preview(self, all_operations, mode, subgroup_enabled=False):
        mode_names = {
            'direct': '⚡ 直接模式',
            'copy': '📋 复制模式',
            'move': '🚚 移动模式',
            'pre': '👀 试运行模式'
        }
        
        print(f"\n🔎 完整操作预览 ({mode_names.get(mode, mode)})")
        print("="*80)
        print(f"🔍 使用的集数匹配正则: {self.episode_regex}")
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
            print(f"├─ 🔤 前缀: {torrent['prefix']}")
            print(f"├─ 🏷️ 季号: S{torrent['season']}")
            if subgroup_enabled and torrent['subgroup']:
                print(f"├─ 🔖 字幕组: {torrent['subgroup']}")
            if torrent.get('custom'):
                print(f"├─ ✍️ 自定义标识: {torrent['custom']}")
            
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
        print("\n🎬 qBittorrent文件整理工具 v12.7")
        print(f"📝 配置文件: {CONFIG['CONFIG_FILE']}")
        print("="*60)
        
        # 显示当前配置
        print("\n📋 当前主要配置:")
        print(f"🌐 WebUI地址: {self.config['QBITTORRENT'].get('host', '未设置')}")
        print(f"👤 用户名: {self.config['QBITTORRENT'].get('username', '未设置')}")
        print(f"🔑 密码: {'*' * len(self.config['QBITTORRENT'].get('password', '')) if self.config['QBITTORRENT'].get('password') else '未设置'}")
        print(f"🏷️ 默认标签: {self.config['QBITTORRENT'].get('default_tag', '未设置')}")
        print(f"📂 工作目录: {self.config['SETTINGS'].get('workspace', '未设置')}")
        print(f"🔍 最大目录深度: {self.config['SETTINGS'].get('max_dir_depth', '1')}")
        
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
