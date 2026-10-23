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

def process_and_import_to_neo4j(folder_path: str):
    if not os.path.exists(folder_path):
        print(f"错误: 路径 '{folder_path}' 不存在！")
        return

    # 1. 扫描文件夹下的所有源 A.json 文件（排除 -entity.json）
    files = os.listdir(folder_path)
    a_files = [f for f in files if f.endswith(".json") and not f.endswith("-entity.json")]

    if not a_files:
        print(f"在 '{folder_path}' 中未找到任何源 JSON 文件。")
        return

    all_raw_entities = []
    document_data = {}

    print(f"找到 {len(a_files)} 个主 JSON 文件，开始加载与统计...\n")

    for a_file in a_files:
        doc_name = os.path.splitext(a_file)[0]
        a_path = os.path.join(folder_path, a_file)
        entity_file = f"{doc_name}-entity.json"
        entity_path = os.path.join(folder_path, entity_file)

        if not os.path.exists(entity_path):
            print(f"⚠ 警告: 未找到对应的实体文件 {entity_file}，跳过此文件。")
            continue

        with open(a_path, 'r', encoding='utf-8') as f:
            chunks_data = json.load(f)

        with open(entity_path, 'r', encoding='utf-8') as f:
            entities_data = json.load(f)

        # 收集原始实体
        for ent in entities_data:
            if not isinstance(ent, dict):
                continue
            ent_record = {
                "source_file": doc_name,
                "chunk_id": ent.get("chunk_id"),
                "type": ent.get("type"),
                "name": ent.get("name"),
                "description": ent.get("description", "")
            }
            all_raw_entities.append(ent_record)

        document_data[doc_name] = chunks_data

    # ==========================================
    # 2. 统计计算与实体合并（仅对同 type 同 name 的 Entity 全局合并，保留较长 description）
    # ==========================================
    total_before = len(all_raw_entities)

    name_counts = defaultdict(int)
    name_type_counts = defaultdict(int)
    for ent in all_raw_entities:
        name_counts[ent["name"]] += 1
        name_type_counts[(ent["name"], ent["type"])] += 1

    entities_with_same_name = sum(1 for ent in all_raw_entities if name_counts[ent["name"]] > 1)
    entities_with_same_name_type = sum(1 for ent in all_raw_entities if name_type_counts[(ent["name"], ent["type"])] > 1)

    merged_entities_map = {}
    grouped_by_type_name = defaultdict(list)
    for ent in all_raw_entities:
        grouped_by_type_name[(ent["type"], ent["name"])].append(ent)

    trace_logs = []
    for key, ents in grouped_by_type_name.items():
        ents_sorted = sorted(ents, key=lambda x: len(x.get("description", "")), reverse=True)
        best_ent = ents_sorted[0]
        merged_entities_map[key] = best_ent

        if len(ents) > 1:
            for other in ents[1:]:
                trace_logs.append({
                    "merged_from_file": other["source_file"],
                    "merged_into_file": best_ent["source_file"],
                    "entity_type": key[0],
                    "entity_name": key[1],
                    "discarded_description": other.get("description"),
                    "kept_description": best_ent.get("description")
                })

    total_after = len(merged_entities_map)

    trace_path = os.path.join(folder_path, "merge_trace.json")
    with open(trace_path, 'w', encoding='utf-8') as f:
        json.dump(trace_logs, f, ensure_ascii=False, indent=4)

    print("=" * 50)
    print("【图谱实体合并统计报告】")
    print(f"  - 合并前 Entity 总数量: {total_before}")
    print(f"  - 合并后 Entity 总数量: {total_after}")
    print(f"  - 存在同名（同 name）情况的实体总数: {entities_with_same_name}")
    print(f"  - 完全同名同类型（同 name & type）的实体总数: {entities_with_same_name_type}")
    print(f"  - 合并追踪日志已保存至: {trace_path}")
    print("=" * 50 + "\n")

    # ==========================================
    # 3. 写入 Neo4j 数据库
    # ==========================================
    print(f"正在连接 Neo4j 数据库 (目标库: {NEO4J_DATABASE}) 并写入图谱...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

    try:
        with driver.session(database=NEO4J_DATABASE) as session:
            for doc_name, chunks_data in document_data.items():
                print(f"  └─ 正在写入 Document: {doc_name}")

                # 创建 Document 节点
                session.run("MERGE (d:Document {name: $docName})", docName=doc_name)

                # 逐个 chunk 处理整个层级：Module -> Unit -> Page -> Chunk -> Entity
                for item in chunks_data:
                    module_name = item.get("part_title", "默认模块")
                    unit_name = item.get("section_title", "默认单元")
                    page_idx = item.get("page_idx")
                    page_name = f"第{page_idx}页"
                    chunk_id = item.get("chunk_id")
                    chunk_text = item.get("text", "")
                    chunk_type = item.get("chunk_type", "")

                    # 1. 逐层级使用局部 MERGE（同级同名合并，跨级/跨文档隔离）
                    session.run("""
                        MATCH (d:Document {name: $docName})
                        MERGE (d)-[:HAS_MODULE]->(m:Module {name: $moduleName})
                        MERGE (m)-[:HAS_UNIT]->(u:Unit {name: $unitName})
                        MERGE (u)-[:HAS_PAGE]->(p:Page {name: $pageName, page_idx: $pageIdx})
                        MERGE (p)-[:HAS_CHUNK]->(ck:Chunk {name: $chunkId})
                        ON CREATE SET ck.chunk = $chunkText, ck.chunk_type = $chunkType
                        ON MATCH SET ck.chunk = $chunkText, ck.chunk_type = $chunkType
                    """, docName=doc_name, moduleName=module_name, unitName=unit_name,
                         pageName=page_name, pageIdx=page_idx, chunkId=chunk_id,
                         chunkText=chunk_text, chunkType=chunk_type)

                    # 2. 匹配当前 Chunk 并关联对应的 Entity（Entity 跨文档、跨层级全局合并）
                    # 寻找当前 chunk 包含的实体（通过 chunk_id 关联）
                    # 注：A-entity.json 中的实体通常带有 chunk_id 字段
                    pass # 实体关联在下方批量或单独匹配

                # 重新遍历当前文档的所有 chunk 对应的实体并挂载到 [:HAS_ENTITY]
                # 加载对应实体文件
                entity_path = os.path.join(folder_path, f"{doc_name}-entity.json")
                if os.path.exists(entity_path):
                    with open(entity_path, 'r', encoding='utf-8') as ef:
                        doc_entities = json.load(ef)

                    for ent in doc_entities:
                        if not isinstance(ent, dict):
                            continue
                        c_id = ent.get("chunk_id")
                        t = ent.get("type")
                        n = ent.get("name")
                        
                        # 获取合并后的最优实体属性
                        best_ent = merged_entities_map.get((t, n))
                        if best_ent and c_id:
                            session.run("""
                                MATCH (ck:Chunk {name: $chunkId})
                                MERGE (e:Entity {type: $type, name: $name})
                                ON CREATE SET e.description = $description
                                ON MATCH SET e.description = CASE 
                                    WHEN size($description) > size(coalesce(e.description, '')) 
                                    THEN $description 
                                    ELSE e.description 
                                END
                                MERGE (ck)-[:HAS_ENTITY]->(e)
                            """, chunkId=c_id, type=best_ent["type"], 
                                 name=best_ent["name"], description=best_ent["description"])

        print("\n✓ 恭喜！多层级图谱构建与合并操作全部完成！")

    except Exception as e:
        print(f"✕ 写入 Neo4j 时发生错误: {e}")
    finally:
        driver.close()

if __name__ == "__main__":
    folder_path = input("请输入存放 JSON 文件的文件夹路径: ").strip()
    process_and_import_to_neo4j(folder_path)