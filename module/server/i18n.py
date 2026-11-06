import json
from pathlib import Path
from xml.etree import ElementTree


class Addition:
    @classmethod
    def load_additions(cls) -> dict:
        result = {}
        files: str = ['en-US', 'zh-CN']
        for file in files:
            file_path = Path.cwd() / 'assets' / 'i18n' / f'{file}.json'
            result[file] = {}
            if not file_path.exists():
                continue
            with open(str(file_path), 'r', encoding='utf-8') as f:
                result[file] = json.load(f)
            # 本地自建词条覆盖层: assets 是上游跟踪文件(改它必与上游冲突),
            # 自建词条统一放 module/config/i18n/, 这里合并后 /home/additional_translate
            # 也能返回, 否则前端界面查不到自建字段会显示英文 key。
            local_path = Path.cwd() / 'module' / 'config' / 'i18n' / f'{file}.json'
            if local_path.exists():
                with open(str(local_path), 'r', encoding='utf-8') as f:
                    result[file].update(json.load(f))
        return result

    @classmethod
    def load_qt_zh_cn(cls) -> dict:
        """读取 Qt 翻译源 zh_CN.xml 的 source->translation。"""
        path = Path.cwd() / 'module' / 'config' / 'i18n' / 'zh_CN.xml'
        result = {}
        try:
            root = ElementTree.parse(path).getroot()
        except Exception:
            return result
        for message in root.iter('message'):
            source = message.findtext('source')
            translation = message.findtext('translation')
            if source and translation:
                result[source] = translation
        return result


class I18n(Addition):
    file_zh_cn = Path.cwd() / 'module' / 'config' / 'i18n' / 'zh-CN.json'

    @classmethod
    def load_json_zh_cn(cls) -> dict:
        """合并 assets 词条与 OASX dump 的完整词典, 后者优先。"""
        merged = dict(cls.load_additions().get('zh-CN', {}))
        merged.update(cls.load_zh_cn())
        return merged

    @classmethod
    def trans_zh_cn(cls, text) -> str:
        """把任务名 / 字段名译成中文, 未收录时原样返回。"""
        merged = cls.load_qt_zh_cn()
        merged.update(cls.load_json_zh_cn())
        return merged[text] if text in merged else text

    @classmethod
    def save_zh_cn(cls, data) -> None:
        I18n.file_zh_cn.parent.mkdir(parents=True, exist_ok=True)
        with open(str(I18n.file_zh_cn), 'w', encoding='utf-8') as f:
            s = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False, default=str)
            f.write(s)

    @classmethod
    def load_zh_cn(cls) -> dict:
        if not I18n.file_zh_cn.exists():
            return {}
        with open(str(I18n.file_zh_cn), 'r', encoding='utf-8') as f:
            return json.load(f)


if __name__ == '__main__':
    print(I18n.load_zh_cn())