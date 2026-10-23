import os
import re
import json
import random
import pandas as pd
import networkx as nx
from openai import OpenAI
from dotenv import load_dotenv
from neo4j import GraphDatabase

# 1. 加载环境变量
load_dotenv()

LITELLM_PROXY_API_BASE = os.getenv("LITELLM_PROXY_API_BASE")
LITELLM_PROXY_API_KEY = os.getenv("LITELLM_PROXY_API_KEY")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "deepseek-chat")

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "0826algo")

OUTPUT_RAW_CSV = "structured_prerequisites_raw_med.csv"
OUTPUT_PRUNED_CSV = "structured_prerequisites_pruned_med.csv"

# 2. 初始化 OpenAI / LiteLLM 客户端
llm_client = OpenAI(
    base_url=LITELLM_PROXY_API_BASE,
    api_key=LITELLM_PROXY_API_KEY,
    timeout=300.0
)

# ---------------------------------------------------------------------------
# 🧹 核心：正则清洗标题中的“第几章/第几节/Chapter X”
# ---------------------------------------------------------------------------
def clean_chapter_title(title: str) -> str:
    """去除标题开头的‘第X章’、‘第X节’、‘Chapter X’、数字序号等标识"""
    if not title:
        return ""
    
    t = title.strip().strip('"').strip("'")
    
    patterns = [
        r'^\s*第[一二三四五六七八九十0-9]+[章节目]\s*',     # 如: "第一章 ", "第2节 "
        r'^\s*Chapter\s+[0-9IVXLCDM]+\s*',                  # 如: "Chapter 1 ", "Chapter IV "
        r'^\s*Section\s+[0-9IVXLCDM]+\s*',                  # 如: "Section 2 "
        r'^\s*([0-9]{1,2}|[IVXLCDM]+)[\.\s、一-十]+\s*',    # 如: "1. ", "IV. ", "一、 "
        r'^\s*Slides?\s*\d+',                               # 如: "Slides01"
    ]
    
    for pattern in patterns:
        t = re.sub(pattern, '', t, flags=re.IGNORECASE)
        
    return t.strip()

# ---------------------------------------------------------------------------
# 全局提取 Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是一位顶级的课程教学大纲与知识图谱专家。
你的任务是从给定的章节列表中，构建一个【极度精炼、高稀疏度】的章节先修依赖图谱 (DAG)。

【严格剪枝规则】：
1. 极致稀疏 (Strict Sparsity)：
   - 绝大多数章节（特别是基础概念、绪论、概述）的先修必须为空 ([])。
   - 每个目标章节 (target) 的直接先修数量上限为 1~2 个，严禁产生 3 个以上的先修！
2. 仅保留直接核心依赖 (Direct Core Only)：
   - 仅当“不掌握 A 就绝对完全无法理解 B 的核心内容”时才建立 A -> B。
3. 严格禁止传递性依赖 (No Transitive Edges)：
   - 若存在 A -> B 且 B -> C，绝对严禁输出 A -> C！只保留最直接的相邻先修关系。

【输出格式要求】：
请按 JSON 格式严格返回，包含一个名为 "prerequisites" 的数组：
{
  "prerequisites": [
    {"source": "文档ID.源节点ID", "target": "文档ID.目标节点ID"}
  ]
}
"""

def fetch_chapters_from_neo4j(lesson_title: str) -> list:
    """从 Neo4j 读取指定 Lesson 下的 Chapter 节点，支持 Lesson -> Topic -> Chapter 结构"""
    print(f"📖 正在从 Neo4j 数据库 [{NEO4J_DATABASE}] 读取 Lesson 《{lesson_title}》 下的 Chapter 节点...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
    
    # 精准匹配 Lesson -> Topic -> Chapter 结构
    cypher_query = """
    MATCH (l:Lesson {title: $lesson_title})-[:HAS_TOPIC]->(t:Topic)-[:CONTAINS]->(a:Document)-[:HAS_SECTION]->(b:Section)-[:HAS_CHAPTER]->(c:Chapter)
    RETURN 
        l.title AS lesson_title,
        coalesce(c.title, c.name, c.chapter, '') AS raw_title,
        coalesce(c.full_id, '') AS full_id,
        coalesce(c.summary, c.description, '') AS summary
    """
    
    chapters = []
    with driver.session(database=NEO4J_DATABASE) as session:
        result = session.run(cypher_query, lesson_title=lesson_title)
        for record in result:
            doc_name = record["lesson_title"].strip()
            raw_title = record["raw_title"].strip().strip('"')
            
            full_id = record["full_id"].strip()
            if not full_id:
                full_id = f"{doc_name}.{raw_title}"
            
            cleaned_title = clean_chapter_title(raw_title)
                
            chapters.append({
                "id": full_id,                     
                "document": doc_name,               
                "clean_title": cleaned_title,       
                "summary": record["summary"]        
            })
            
    driver.close()
    print(f"✅ 成功读取并清洗了 {len(chapters)} 个 Chapter 节点！")
    return chapters

# ---------------------------------------------------------------------------
# 🛠️ 图论后处理：传递归约剪枝 (Transitive Reduction)
# ---------------------------------------------------------------------------
def prune_graph_edges(prereqs: list) -> list:
    """
    使用 NetworkX 进行图论后处理：
    1. 构建有向图
    2. 破环（移除循环依赖）
    3. 传递归约 (Transitive Reduction)：剔除所有中间长链冗余边
    """
    print("\n🛠️ 开始使用图论算法对原始关系进行硬剪枝...")
    
    G = nx.DiGraph()
    for item in prereqs:
        src = item.get("source")
        tgt = item.get("target")
        if src and tgt and src != tgt:
            G.add_edge(src, tgt)

    raw_edge_count = G.number_of_edges()
    print(f"  ├─ 原始关系边数量: {raw_edge_count}")

    # 1. 破环检查 (Remove Cycles)
    while not nx.is_directed_acyclic_graph(G):
        cycles = list(nx.simple_cycles(G))
        if not cycles:
            break
        cycle = cycles[0]
        u, v = cycle[-1], cycle[0]
        G.remove_edge(u, v)
        print(f"  ├─ ⚠️ 检测到逻辑环路 {cycle}，自动移除边: {u} -> {v}")

    # 2. 传递归约 (Transitive Reduction)
    TR = nx.transitive_reduction(G)

    pruned_edges = []
    for u, v in TR.edges():
        pruned_edges.append({
            "source": u,
            "target": v
        })

    pruned_edge_count = len(pruned_edges)
    cut_count = raw_edge_count - pruned_edge_count
    reduction_rate = (cut_count / max(raw_edge_count, 1)) * 100
    print(f"  └─ ✅ 剪枝完成！保留核心直接边: {pruned_edge_count} 条，移除冗余边: {cut_count} 条 (压缩率: {reduction_rate:.1f}%)\n")

    return pruned_edges

def main():
    # 1. 获取用户指定的 Lesson Title
    target_lesson = input("请输入要分析的 Lesson Title: ").strip()
    if not target_lesson:
        print("❌ Lesson Title 不能为空！")
        return

    # 2. 获取并清洗指定 Lesson 下的节点数据
    chapters = fetch_chapters_from_neo4j(target_lesson)
    if not chapters:
        print(f"❌ 未在数据库中找到属于 Lesson 《{target_lesson}》 的章节数据。")
        return

    # 2. 随机打乱章节顺序
    print("🔀 正在随机打乱章节顺序...")
    random.shuffle(chapters)

    # 3. 构造极简 Payload（仅保留 ID 和 清洗后的标题，去掉长摘要以防止超出 Token 上限）
    simplified_chapters = [
        {
            "id": ch["id"],
            "title": ch["clean_title"]
        }
        for ch in chapters
    ]

    user_prompt = f"""以下是课程中共 {len(simplified_chapters)} 个章节的概念列表（已去除序号且顺序已随机打乱）：

{json.dumps(simplified_chapters, ensure_ascii=False)}

请完全根据【概念与知识逻辑】，审查所有章节，提取出所有【直接且绝对必要】的先修关系，并按 JSON 格式输出。
"""

    # 4. 调用 LLM 发送数据
    print(f"🚀 使用模型 [{MODEL_NAME}] 进行全局先修关系推理...")
    try:
        response = llm_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=8192  # 👈 关键点 1：调大 Token 输出上限，防止长 JSON 截断
        )

        res_content = response.choices[0].message.content

        # 👈 关键点 2：增加 JSON 解析截断容错处理
        try:
            res_json = json.loads(res_content)
        except json.JSONDecodeError as e:
            print("⚠️ 警告：LLM 输出的 JSON 未正常闭合，正在尝试自动截断补全...")
            last_bracket = res_content.rfind("}")
            if last_bracket != -1:
                fixed_content = res_content[:last_bracket+1] + "\n]}"
                res_json = json.loads(fixed_content)
            else:
                raise e

        raw_prereqs = res_json.get("prerequisites", [])
        print(f"✅ 全局推理完成！原始提取出 {len(raw_prereqs)} 条关系。")

        # 5. 保存 LLM 原始提取结果
        raw_df = pd.DataFrame([
            {"source_doc_chapter": item["source"], "target_doc_chapter": item["target"]}
            for item in raw_prereqs
        ])
        raw_df.to_csv(OUTPUT_RAW_CSV, index=False, encoding="utf-8-sig")

        # 6. 图论算法拓扑归约剪枝
        pruned_prereqs = prune_graph_edges(raw_prereqs)

        # 7. 保存最终剪枝后的高质量拓扑关系
        pruned_df = pd.DataFrame([
            {"source_doc_chapter": item["source"], "target_doc_chapter": item["target"]}
            for item in pruned_prereqs
        ])
        pruned_df.to_csv(OUTPUT_PRUNED_CSV, index=False, encoding="utf-8-sig")
        print(f"💾 最终拓扑剪枝后的数据已成功导出至: '{OUTPUT_PRUNED_CSV}'")

    except Exception as e:
        print(f"❌ API 请求或解析失败: {e}")

if __name__ == "__main__":
    main()