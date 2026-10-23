import os
import json

def update_entity_type_in_folder(folder_path: str, old_type: str, new_type: str):
    """
    遍历指定文件夹下的所有 JSON 文件，将实体中的 old_type 批量更新为 new_type
    """
    if not os.path.exists(folder_path):
        print(f"错误: 文件夹路径 '{folder_path}' 不存在！")
        return

    json_files = []
    for root, _, files in os.walk(folder_path):
        for f in files:
            if f.endswith('.json'):
                json_files.append(os.path.join(root, f))

    if not json_files:
        print(f"在 '{folder_path}' 中未找到任何 JSON 文件。")
        return

    print(f"\n[开始处理] 扫描 {len(json_files)} 个 JSON 文件...")
    updated_files_count = 0
    total_replaced_count = 0

    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            if not isinstance(data, list):
                continue

            replaced_in_file = 0
            for item in data:
                if isinstance(item, dict) and item.get("type") == old_type:
                    item["type"] = new_type
                    replaced_in_file += 1

            if replaced_in_file > 0:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                
                updated_files_count += 1
                total_replaced_count += replaced_in_file
                print(f"✓ 已更新: {os.path.basename(file_path)} (替换了 {replaced_in_file} 处)")

        except Exception as e:
            print(f"✕ 处理文件 {file_path} 失败: {e}")

    print("-" * 50)
    print(f"本轮结果: 更新 {updated_files_count} 个文件，替换了 {total_replaced_count} 处 ('{old_type}' -> '{new_type}')")
    print("-" * 50)

if __name__ == "__main__":
    folder_path = input("请输入文件夹路径: ").strip()
    
    # 循环多轮替换
    while True:
        print("\n" + "=" * 50)
        old_type = input("请输入需要被替换的 type 名称 (直接回车或输入 q 退出): ").strip()
        if not old_type or old_type.lower() == 'q':
            print("已退出替换程序。")
            break
            
        new_type = input("请输入替换后的 type 名称: ").strip()
        
        # 执行本轮替换
        update_entity_type_in_folder(folder_path, old_type, new_type)