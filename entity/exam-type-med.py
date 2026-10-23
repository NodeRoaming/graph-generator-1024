import os
import json

# 预期的 4 个标准类型
STANDARD_TYPES = {
    "生理结构与细胞",
    "化学物质与生物分子",
    "生理过程与调节机制",
    "疾病与临床表现"
}

def check_entity_types(dir_path: str):
    if not os.path.exists(dir_path):
        print(f"错误: 路径 '{dir_path}' 不存在！")
        return

    # 统计所有出现的 type 及其频率
    type_counts = {}
    
    # 获取所有的 .json 文件
    json_files = [
        os.path.join(dp, f) 
        for dp, dn, filenames in os.walk(dir_path) 
        for f in filenames if f.endswith("-entity.json") or f.endswith(".json")
    ]

    print(f"扫描到 {len(json_files)} 个 JSON 文件，正在检查 type 字段...\n")

    for file_path in json_files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and "type" in item:
                            t = item["type"]
                            type_counts[t] = type_counts.get(t, 0) + 1
        except Exception as e:
            print(f"读取文件 {file_path} 失败: {e}")

    # 结果输出
    all_found_types = set(type_counts.keys())
    extra_types = all_found_types - STANDARD_TYPES

    print("=" * 50)
    print("【所有检测到的 type 汇总及出现次数】:")
    for t, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
        status = "✓ 标准" if t in STANDARD_TYPES else "★ 额外/非标准"
        print(f"  - [{status}] {t}: {count} 次")

    print("\n" + "=" * 50)
    if extra_types:
        print(f"【发现 {len(extra_types)} 个预设 4 类之外的 type】:")
        for t in extra_types:
            print(f"  * {t}")
    else:
        print("【检查完毕】所有实体的 type 均严格属于预设的 4 个标准分类，没有其他类型！")

if __name__ == "__main__":
    target_dir = input("请输入实体 JSON 文件所在的文件夹路径: ").strip()
    check_entity_types(target_dir)