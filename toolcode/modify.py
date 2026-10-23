import json
import os

def process_json():
    # 获取 JSON 文件路径
    json_path = input("请输入 JSON 文件路径: ").strip()
    
    # 自动从文件路径中提取无扩展名的文件名（例如：从 "path/to/医学1.json" 中提取出 "医学1"）
    base_name = os.path.splitext(os.path.basename(json_path))[0]
    
    # 读取 JSON 数据
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"读取文件失败: {e}")
        return

    # 获取要填充的 chapter 内容
    # chapter_value = input("请输入要填充的 chapter 内容: ").strip()

    # 处理列表数据中的每个对象
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                # 删除 question 字段
                item.pop("question", None)
                # 填充 chapter 属性
                # item["chapter"] = chapter_value
                # 将 page_idx 加 1
                # if "page_idx" in item:
                    # item["page_idx"] = item["page_idx"] + 1
                    # 添加 screenshot 字段，格式为 <文件名>/<页码>.png
                    # item["screenshot"] = f"{base_name}/{item['page_idx']}.png"
    else:
        print("JSON 根数据不是列表结构，请检查输入文件。")
        return

    # 保存处理后的文件
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

    print(f"处理完成！修改后的 JSON 已保存至：{json_path}")

if __name__ == "__main__":
    process_json()