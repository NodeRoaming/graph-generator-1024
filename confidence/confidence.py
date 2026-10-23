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
# NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
NEO4J_DATABASE = "0826algo"

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))

def fetch_graph_data():
    with driver.session(database=NEO4J_DATABASE) as session:
        # 查询实体关系三元组（适配 Neo4j 5.x 语法，获取名称）
        triples_query = """
        MATCH (h:Entity)-[r]->(t:Entity)
        WHERE NOT type(r) IN ['HAS_TOPIC','HAS_MODULE','HAS_UNIT','HAS_PAGE','HAS_CHUNK','CONTAINS','HAS_SECTION', 'HAS_CHAPTER', 'HAS_SLIDE', 'MENTIONS']
        RETURN 
            elementId(h) AS h_id,
            coalesce(h.name, h.id, elementId(h)) AS h_name,
            type(r) AS r, 
            elementId(t) AS t_id,
            coalesce(t.name, t.id, elementId(t)) AS t_name,
            coalesce(r.strength, r.weight, 1.0) AS strength
        """
        triples = session.run(triples_query).data()

        # 查询实体属性类型
        attrs_query = """
        MATCH (e:Entity)
        RETURN elementId(e) AS entity_id, coalesce(e.type, e.types, ['Unknown']) AS attr_types
        """
        attrs = session.run(attrs_query).data()

    return triples, attrs

# 2. AEKE 评估模型
class GraphEvaluator(nn.Module):
    def __init__(self, num_nodes, num_rels, embed_dim=64, lambda1=1.0, lambda2=1.0):
        super().__init__()
        self.node_emb = nn.Embedding(num_nodes, embed_dim)
        self.rel_emb = nn.Embedding(num_rels, embed_dim)
        
        # 局部Bi-LSTM：提取三元组局部表征p_i
        self.bilstm = nn.LSTM(embed_dim, embed_dim, batch_first=True, bidirectional=True)
        self.fc_local = nn.Linear(embed_dim * 2, embed_dim)
        
        # 论文公式(18)
        self.lambda1 = lambda1
        self.lambda2 = lambda2

    def forward(self, h, r, t, global_context_emb, attr_context_emb):
        # global_context_emb: x_i, 关系超图
        # attr_context_emb: z_i, 属性超图

        e_h = self.node_emb(h)
        e_r = self.rel_emb(r)
        e_t = self.node_emb(t)

        # 论文公式(15)
        l2_dist = torch.norm(e_h + e_r - e_t, p=2, dim=-1)
        LT = 1.0 / (1.0 + torch.exp(-l2_dist))

        # 提取p_i
        seq = torch.stack([e_h, e_r, e_t], dim=1)
        lstm_out, _ = self.bilstm(seq)
        p_i = self.fc_local(lstm_out[:, -1, :])

        # 论文公式(16)
        GT = F.cosine_similarity(p_i, global_context_emb, dim=-1)

        # 论文公式(17)
        AT = F.cosine_similarity(p_i, attr_context_emb, dim=-1)

        # 论文公式(18)
        score = LT + self.lambda1 * GT + self.lambda2 * AT
        confidence = torch.sigmoid(score)

        return confidence

# 3. 运行评估与生成详细统计数据
def run_detailed_assessment():
    triples, attrs = fetch_graph_data()
    print(f"数据加载完成，共检索到 {len(triples)} 条实体元组。开始评估...\n")

    # 构建索引
    node_ids = list(set([t['h_id'] for t in triples] + [t['t_id'] for t in triples]))
    rel_types = list(set([t['r'] for t in triples]))
    node2idx = {nid: i for i, nid in enumerate(node_ids)}
    rel2idx = {r: i for i, r in enumerate(rel_types)}

    model = GraphEvaluator(num_nodes=len(node2idx), num_rels=len(rel2idx))
    model.eval()

    eval_records = []
    with torch.no_grad():
        for t in triples:
            # 传给 model 的输入必须是 LongTensor 索引
            h_idx = torch.tensor([node2idx[t['h_id']]], dtype=torch.long)
            r_idx = torch.tensor([rel2idx[t['r']]], dtype=torch.long)
            t_idx = torch.tensor([node2idx[t['t_id']]], dtype=torch.long)

            # 通过索引获取用于构建 context 的临时 embedding
            h_e = model.node_emb(h_idx)
            t_e = model.node_emb(t_idx)
            ctx_e = (h_e + t_e) / 2.0
            
            conf = model(h_idx, r_idx, t_idx, ctx_e, ctx_e)
            score = round(conf.item(), 4)
            
            eval_records.append({
                "head_name": t['h_name'],
                "relation": t['r'],
                "tail_name": t['t_name'],
                "strength": t['strength'],
                "confidence": score,
                "risk_level": "高风险(错误)" if score < 0.3 else ("中风险(疑义)" if score < 0.7 else "低风险(优质)")
            })

    # 转为 DataFrame 进行统计分析
    df = pd.DataFrame(eval_records)

    # ---------------- 统计数据展示 ----------------
    print("=" * 60)
    print("               知识图谱总体质量评估统计               ")
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

    print("\n--- 2. 按关系类型 (Relation Type) 统计质量指标 ---")
    type_stats = df.groupby('relation').agg(
        元组数量=('confidence', 'count'),
        平均置信度=('confidence', 'mean'),
        低置信度数量_低于0_3=('confidence', lambda x: (x < 0.3).sum()),
        高置信度占比=('confidence', lambda x: f"{(x >= 0.7).sum() / len(x) * 100:.1f}%")
    ).reset_index()
    print(type_stats.to_string(index=False))

    print("\n--- 3. 异常冲突检测 (高原始强度但低置信度的异常关系 Top 5) ---")
    # 高 strength 代表人工/抽取给的分高，但置信度低代表存在逻辑矛盾
    df['anomaly_score'] = df['strength'] * (1 - df['confidence'])
    anomalies = df.sort_values(by='anomaly_score', ascending=False).head(5)
    for _, row in anomalies.iterrows():
        print(f"  * ({row['head_name']}) -[{row['relation']}]-> ({row['tail_name']}) | 强度: {row['strength']} | 置信度: {row['confidence']}")

    # 导出文件
    csv_path = "kg_assessment_report_1017.csv"
    json_path = "kg_assessment_report_1017.json"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    df.to_json(json_path, orient="records", force_ascii=False, indent=2)
    print(f"\n已成功将详细明细数据导出至: {csv_path} 和 {json_path}")

if __name__ == "__main__":
    run_detailed_assessment()
    driver.close()