import os
import json
from collections import defaultdict
from dotenv import load_dotenv
from neo4j import GraphDatabase

# 加载环境变量
load_dotenv()
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

def import_and_merge_relations(folder_path: str):
    if not os.path.exists(folder_path):
        print(f"错误: 路径 '{folder_path}' 不存在！")
        return

    files = os.listdir(folder_path)
    relation_files = [f for f in files if f.endswith("-relations_filtered.json")]

    if not relation_files:
        print(f"在 '{folder_path}' 中未找到任何 *-relations_filtered.json 文件。")
        return

    # 用于在内存中聚合关系
    # Key: (src_type, src_name, tgt_type, tgt_name, relationship_type)
    # Value: { "reasons": [...], "strengths": [...] }
    relation_aggregation = defaultdict(lambda: {"reasons": [], "strengths": [], "source": None, "target": None})

    print(f"找到 {len(relation_files)} 个关系文件，开始在内存中加载与聚合...\n")

    total_raw_count = 0
    for rel_file in relation_files:
        file_path = os.path.join(folder_path, rel_file)
        with open(file_path, 'r', encoding='utf-8') as f:
            relations_data = json.load(f)

        if not isinstance(relations_data, list):
            continue

        for item in relations_data:
            if not isinstance(item, dict):
                continue

            rel_type = item.get("relationship")
            reason = item.get("reason", "")
            strength = item.get("strength")
            
            source_node = item.get("source", {})
            target_node = item.get("target", {})

            src_type = source_node.get("type")
            src_name = source_node.get("name")
            tgt_type = target_node.get("type")
            tgt_name = target_node.get("name")

            if not rel_type or not src_type or not src_name or not tgt_type or not tgt_name:
                continue

            total_raw_count += 1
            key = (src_type, src_name, tgt_type, tgt_name, rel_type)

            if reason:
                relation_aggregation[key]["reasons"].append(reason)
            if strength is not None:
                relation_aggregation[key]["strengths"].append(float(strength))
            
            # 保存节点基础信息用于后续写入
            relation_aggregation[key]["source"] = source_node
            relation_aggregation[key]["target"] = target_node

    print(f"  - 原始提取出的关系总条数: {total_raw_count}")
    print(f"  - 去重聚合后的独立关系总条数: {len(relation_aggregation)}\n")

    # ==========================================
    # 写入 Neo4j 数据库
    # ==========================================
    print(f"正在连接 Neo4j 数据库 (目标库: {NEO4J_DATABASE}) 并写入聚合后的关系...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            success_count = 0
            
            for key, agg_data in relation_aggregation.items():
                src_type, src_name, tgt_type, tgt_name, rel_type = key
                
                # 规则 1：reason 选择最长的一条
                reasons = agg_data["reasons"]
                best_reason = max(reasons, key=len) if reasons else ""

                # 规则 2：strength 取平均值（并四舍五入保留 1 位小数或取整）
                strengths = agg_data["strengths"]
                avg_strength = round(sum(strengths) / len(strengths), 1) if strengths else 5.0

                # 使用 APOC 动态创建关系
                query = """
                    MATCH (src:Entity {type: $srcType, name: $srcName})
                    MATCH (tgt:Entity {type: $tgtType, name: $tgtName})
                    CALL apoc.create.relationship(src, $relType, {reason: $reason, strength: $strength}, tgt) 
                    YIELD rel
                    RETURN count(rel) as cnt
                """
                
                result = session.run(
                    query,
                    srcType=src_type,
                    srcName=src_name,
                    tgtType=tgt_type,
                    tgtName=tgt_name,
                    relType=rel_type,
                    reason=best_reason,
                    strength=avg_strength
                )
                
                record = result.single()
                if record and record["cnt"] > 0:
                    success_count += 1

        print(f"\n✓ 成功将 {success_count} 条去重并合并属性后的关系写入 Neo4j 数据库！")

    except Exception as e:
        print(f"✕ 写入关系时发生错误: {e}")
        print("💡 提示：请确保 Neo4j 数据库已成功安装并启动了 APOC 插件。")
    finally:
        driver.close()

if __name__ == "__main__":
    folder_path = input("请输入包含 *-relations.json 文件的文件夹路径: ").strip()
    import_and_merge_relations(folder_path)