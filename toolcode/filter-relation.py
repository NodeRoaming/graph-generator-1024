import os
import json

def clean_relation_files(folder_path: str):
    if not os.path.exists(folder_path):
        print(f"错误: 路径 '{folder_path}' 不存在！")
        return

    files = os.listdir(folder_path)
    # 查找所有 *-relations.json 文件（且排除已经是 _filtered.json 的文件）
    relation_files = [
        f for f in files 
        if f.endswith("-relations.json") and not f.endswith("_filtered-relations.json")
    ]

    if not relation_files:
        print(f"在 '{folder_path}' 中未找到任何 *-relations.json 文件。")
        return

    print(f"找到 {len(relation_files)} 个关系文件，开始清洗...\n")

    total_processed = 0
    total_kept = 0
    total_removed = 0

    for rel_file in relation_files:
        file_path = os.path.join(folder_path, rel_file)
        base_name = rel_file.removesuffix(".json")
        out_file_name = f"{base_name}_filtered.json"
        out_file_path = os.path.join(folder_path, out_file_name)

        print(f"  └─ 处理文件: {rel_file}")

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                relations_data = json.load(f)

            if not isinstance(relations_data, list):
                print(f"     ⚠ 跳过：文件内容不是列表格式。")
                continue

            cleaned_relations = []
            file_removed = 0

            for item in relations_data:
                if not isinstance(item, dict):
                    continue

                total_processed += 1
                strength = item.get("strength")

                # 如果 strength 为空或小于等于 0，则视为无效关系剔除
                if strength is None or strength <= 0:
                    file_removed += 1
                    continue

                cleaned_relations.append(item)

            total_kept += len(cleaned_relations)
            total_removed += file_removed

            # 保存为新的过滤后 JSON 文件
            with open(out_file_path, 'w', encoding='utf-8') as out_f:
                json.dump(cleaned_relations, out_f, ensure_ascii=False, indent=4)

            print(f"     ✓ 保留有效关系: {len(cleaned_relations)} 条 | 剔除无效关系 (strength <= 0): {file_removed} 条")
            print(f"     ➔ 已另存为: {out_file_name}\n")

        except Exception as e:
            print(f"     ✕ 处理文件失败: {e}\n")

    print("=" * 50)
    print("【关系清洗统计报告】")
    print(f"  - 检查总关系数: {total_processed}")
    print(f"  - 成功保留数: {total_kept}")
    print(f"  - 剔除无效数: {total_removed}")
    print("=" * 50)

if __name__ == "__main__":
    folder_path = input("请输入包含关系 JSON 文件的文件夹路径: ").strip()
    clean_relation_files(folder_path)