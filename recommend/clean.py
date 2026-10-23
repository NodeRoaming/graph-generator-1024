import os
import csv

def filter_csv_by_ratio(input_filename, output_filename, ratio_column_name):
    """
    读取指定的 CSV 文件，过滤掉 ratio 低于 25 的行，并保存
    """
    if not os.path.exists(input_filename):
        print(f"未找到文件: {input_filename}，跳过过滤。")
        return

    print(f"正在处理文件: {input_filename}，过滤 {ratio_column_name} < 25 的记录...")
    
    filtered_rows = []
    total_count = 0
    removed_count = 0
    
    with open(input_filename, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        
        for row in reader:
            total_count += 1
            try:
                # 获取占比数值，转换为 float
                ratio_val = float(row.get(ratio_column_name, 0))
                if ratio_val >= 25.0:
                    filtered_rows.append(row)
                else:
                    removed_count += 1
            except ValueError:
                # 如果解析失败，默认保留或跳过（这里选择跳过异常行）
                removed_count += 1

    # 写入过滤后的文件
    with open(output_filename, mode="w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered_rows)
        
    print(f"处理完成！原总行数: {total_count}，已删除低於 25 的行数: {removed_count}，剩余行数: {len(filtered_rows)}")
    print(f"过滤后的结果已保存至: {output_filename}\n")

if __name__ == "__main__":
    # 1. 处理 Section-Module 匹配的 CSV
    # 假设你关注的是 section_ratio(%)，如果是 module_ratio(%) 可以自行修改列名
    filter_csv_by_ratio(
        input_filename="section_module_by_entities_with_ratios.csv",
        output_filename="section_module_filtered.csv",
        ratio_column_name="section_ratio(%)"
    )
    
    # 2. 处理 Chapter-Unit 匹配的 CSV
    # 假设你关注的是 chapter_ratio(%)
    filter_csv_by_ratio(
        input_filename="chapter_unit_similarity.csv",
        output_filename="chapter_unit_filtered.csv",
        ratio_column_name="chapter_ratio(%)"
    )