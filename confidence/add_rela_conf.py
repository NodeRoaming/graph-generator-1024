import os
import json
import pandas as pd
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

# 1. 连接 Neo4j 数据库
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = "0826algo"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))


def fetch_graph_data(target_relations=None):
    """
    拉取图谱数据
    :param target_relations: 指定要计算置信度的关系类型列表，如 ['HAS_CHAPTER', 'PREREQUISITE_FOR']
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        # 如果指定了关系类型列表，构造 WHERE 过滤条件
        where_clause = ""
        if target_relations:
            where_clause = "WHERE type(r) IN $target_rels"
        else:
            # 默认逻辑：过滤掉部分非目标关系
            where_clause = "WHERE NOT type(r) IN ['HAS_DOCUMENT', 'HAS_SECTION', 'HAS_CHAPTER', 'HAS_SLIDE', 'HAS_AUDIO', 'INCLUDES', 'MENTIONS', 'NEXT']"

        triples_query = f"""
        MATCH (h:Entity)-[r]->(t:Entity)
        {where_clause}
        RETURN 
            elementId(h) AS h_id,
            coalesce(h.name, h.id, elementId(h)) AS h_name,
            elementId(r) AS r_id,
            type(r) AS r, 
            elementId(t) AS t_id,
            coalesce(t.name, t.id, elementId(t)) AS t_name,
            coalesce(r.strength, r.weight, 1.0) AS strength
        """
        
        params = {"target_rels": target_relations} if target_relations else {}
        triples = session.run(triples_query, **params).data()

        # 查询实体属性类型
        attrs_query = """
        MATCH (e:Entity)
        RETURN elementId(e) AS entity_id, coalesce(e.type, e.types, ['Unknown']) AS attr_types
        """
        attrs = session.run(attrs_query).data()

    return triples, attrs


def batch_write_confidence(updates):
    """
    批量将置信度写回 Neo4j 关系节点
    :param updates: list of dict, 形如 [{'r_id': '4:xxx:xxx', 'confidence': 0.85}, ...]
    """
    if not updates:
        print("没有需要更新的关系置信度。")
        return

    update_query = """
    UNWIND $batch AS item
    MATCH ()-[r]->() WHERE elementId(r) = item.r_id
    SET r.confidence = item.confidence
    """
    with driver.session(database=NEO4J_DATABASE) as session:
        session.run(update_query, batch=updates)
    
    print(f"✅ 成功将 confidence 属性写回 {len(updates)} 条关系中！")


# 2. AEKE 评估模型
class GraphEvaluator(nn.Module):
    def __init__(self, num_nodes, num_rels, embed_dim=64, lambda1=1.0, lambda2=1.0):
        super().__init__()
        self.node_emb = nn.Embedding(num_nodes, embed_dim)
        self.rel_emb = nn.Embedding(num_rels, embed_dim)
        
        # 局部Bi-LSTM：提取三元组局部表征p_i
        self.bilstm = nn.LSTM(embed_dim, embed_dim, batch_first=True, bidirectional=True)
        self.fc_local = nn.Linear(embed_dim * 2, embed_dim)
        
        self.lambda1 = lambda1
        self.lambda2 = lambda2

    def forward(self, h, r, t, global_context_emb, attr_context_emb):
        e_h = self.node_emb(h)
        e_r = self.rel_emb(r)
        e_t = self.node_emb(t)

        l2_dist = torch.norm(e_h + e_r - e_t, p=2, dim=-1)
        LT = 1.0 / (1.0 + torch.exp(-l2_dist))

        seq = torch.stack([e_h, e_r, e_t], dim=1)
        lstm_out, _ = self.bilstm(seq)
        p_i = self.fc_local(lstm_out[:, -1, :])

        GT = F.cosine_similarity(p_i, global_context_emb, dim=-1)
        AT = F.cosine_similarity(p_i, attr_context_emb, dim=-1)

        score = LT + self.lambda1 * GT + self.lambda2 * AT
        confidence = torch.sigmoid(score)

        return confidence


# 3. 运行评估、更新属性与生成统计
def run_detailed_assessment(target_relations=None, write_to_db=True):
    """
    :param target_relations: 需要计算并更新置信度的关系名字列表，如 ["PREREQUISITE_FOR", "RELATED_TO"]
    :param write_to_db: 是否将 confidence 属性写回数据库
    """
    triples, attrs = fetch_graph_data(target_relations=target_relations)
    
    if not triples:
        print("未检索到符合条件的三元组关系！")
        return

    print(f"数据加载完成，共检索到 {len(triples)} 条指定关系的实体元组。开始评估...\n")

    # 构建索引
    node_ids = list(set([t['h_id'] for t in triples] + [t['t_id'] for t in triples]))
    rel_types = list(set([t['r'] for t in triples]))
    node2idx = {nid: i for i, nid in enumerate(node_ids)}
    rel2idx = {r: i for i, r in enumerate(rel_types)}

    model = GraphEvaluator(num_nodes=len(node2idx), num_rels=len(rel2idx))
    model.eval()

    eval_records = []
    updates_for_db = []  # 存写入数据库的 Payload

    with torch.no_grad():
        for t in triples:
            h_idx = torch.tensor([node2idx[t['h_id']]], dtype=torch.long)
            r_idx = torch.tensor([rel2idx[t['r']]], dtype=torch.long)
            t_idx = torch.tensor([node2idx[t['t_id']]], dtype=torch.long)

            h_e = model.node_emb(h_idx)
            t_e = model.node_emb(t_idx)
            ctx_e = (h_e + t_e) / 2.0
            
            conf = model(h_idx, r_idx, t_idx, ctx_e, ctx_e)
            score = round(conf.item(), 4)
            
            # 记录数据
            eval_records.append({
                "r_id": t['r_id'],
                "head_name": t['h_name'],
                "relation": t['r'],
                "tail_name": t['t_name'],
                "strength": t['strength'],
                "confidence": score,
                "risk_level": "高风险(错误)" if score < 0.3 else ("中风险(疑义)" if score < 0.7 else "低风险(优质)")
            })

            # 准备写入 Neo4j 的数据结构
            updates_for_db.append({
                "r_id": t['r_id'],
                "confidence": score
            })

    # ---------------- 写回 Neo4j 数据库 ----------------
    if write_to_db:
        print("正在将 confidence 属性写回 Neo4j...")
        batch_write_confidence(updates_for_db)

    # ---------------- 统计数据展示 ----------------
    df = pd.DataFrame(eval_records)
    print("\n" + "=" * 60)
    print("               知识图谱质量评估统计               ")
    print("=" * 60)
    print(f"评估元组总数: {len(df)}")
    print(f"平均置信度 (Mean Confidence): {df['confidence'].mean():.4f}")
    print(f"置信度中位数 (Median Confidence): {df['confidence'].median():.4f}")
    print(f"标准差 (Std Dev): {df['confidence'].std():.4f}\n")

    print("--- 1. 置信度区间统计分布 ---")
    interval_counts = pd.cut(df['confidence'], bins=[0, 0.3, 0.7, 1.0], labels=["高风险 [0.0, 0.3)", "中风险 [0.3, 0.7)", "高质量 [0.7, 1.0]"]).value_counts().sort_index()
    for interval, count in interval_counts.items():
        ratio = (count / len(df)) * 100
        print(f"  * {interval}: {count} 条 ({ratio:.2f}%)")

    # 导出文件
    csv_path = "kg_assessment_report_algo_0912.csv"
    json_path = "kg_assessment_report_algo_0912.json"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_json(json_path, orient="records", force_ascii=False, indent=2)
    print(f"\n已成功将详细明细数据导出至: {csv_path} 和 {json_path}")

if __name__ == "__main__":
    # 指定需要计算置信度并添加 confidence 属性的关系名字列表
    # 如果传 None，则按原有默认逻辑拉取关系
    # TARGET_RELATIONS = ["Associative.临床表现", "Causal.促进_激活","Causal.导致_引发","Causal.抑制_阻断", "Generative.分泌_产生","Structural.包含"]
    TARGET_RELATIONS = ["Associative_性质", "Causal_归结","Causal_求解_构造","Causal_限制", "Causal_优化_推导为", "Generative_求解_构造","Structural_包含"]
    
    run_detailed_assessment(target_relations=TARGET_RELATIONS, write_to_db=True)
    driver.close()