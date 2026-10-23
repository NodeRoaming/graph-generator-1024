import os
import json
import re
from collections import defaultdict
from dotenv import load_dotenv
from openai import OpenAI
from jinja2 import Environment, FileSystemLoader

# 加载环境变量与配置
load_dotenv()
LITELLM_PROXY_API_BASE = os.getenv("LITELLM_PROXY_API_BASE")
LITELLM_PROXY_API_KEY = os.getenv("LITELLM_PROXY_API_KEY")

llm_client = OpenAI(
    base_url=LITELLM_PROXY_API_BASE,
    api_key=LITELLM_PROXY_API_KEY
)

jinja_env = Environment(loader=FileSystemLoader(searchpath="."))

def extract_relations_for_group(chunk_id: str, chunk_text: str, entities: list, model_name: str = "deepseek-chat") -> list:
    """调用 LLM 从一组实体中自主提取并识别关系（仅返回名称和评估信息）"""
    if len(entities) < 2:
        return []

    # 建立实体 name 到完整实体对象的映射字典，方便后续直接复制
    entity_dict = {ent.get("name"): ent for ent in entities if "name" in ent}

    try:
        template = jinja_env.get_template("prompt-med.j2")
        prompt_content = template.render(
            chunk_text=chunk_text,
            entities=entities
        )

        response = llm_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是一个严谨的知识图谱关系抽取专家。请严格按照要求的 JSON 数组格式输出。"},
                {"role": "user", "content": prompt_content}
            ],
            temperature=0.1
        )
        content = response.choices[0].message.content.strip()

        # 清理 Markdown 代码块包裹符
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
            content = re.sub(r"\n?```$", "", content)

        relations = json.loads(content)
        if not isinstance(relations, list):
            return []

        formatted_relations = []
        for rel in relations:
            if isinstance(rel, dict):
                src_name = rel.get("source_name")
                tgt_name = rel.get("target_name")

                # 如果 LLM 返回的名字在当前组的实体中存在，则直接完整复制源对象和目标对象
                if src_name in entity_dict and tgt_name in entity_dict:
                    src_entity = entity_dict[src_name]
                    tgt_entity = entity_dict[tgt_name]

                    formatted_rel = {
                        "chunk_id": chunk_id,
                        "relationship": rel.get("relationship", ""),
                        "reason": rel.get("reason", ""),
                        "strength": rel.get("strength", 5),
                        "source": {"chunk_id": chunk_id, **src_entity},
                        "target": {"chunk_id": chunk_id, **tgt_entity}
                    }
                    formatted_relations.append(formatted_rel)

        return formatted_relations

    except Exception as e:
        print(f"  ✕ [Chunk {chunk_id}] 关系提取异常: {e}")
        return []

def process_folder_relations(folder_path: str, model_name: str = "deepseek-chat"):
    """
    输入文件夹路径，遍历其中的 JSON 文件与对应的 `-entity.json` 文件，
    按页/chunk 聚合实体并让 LLM 提取关系，直接复制完整实体内容输出到新文件夹中。
    """
    if not os.path.exists(folder_path):
        print(f"错误: 路径 '{folder_path}' 不存在！")
        return

    output_dir = os.path.join(folder_path, "extracted_relations")
    os.makedirs(output_dir, exist_ok=True)

    json_files = [
        f for f in os.listdir(folder_path) 
        if f.endswith(".json") and not f.endswith("-entity.json") and os.path.isfile(os.path.join(folder_path, f))
    ]

    if not json_files:
        print(f"在 '{folder_path}' 根目录下未找到有效的源 JSON 文件。")
        return

    print(f"找到 {len(json_files)} 个源文件，开始提取实体关系...\n")

    for file_name in json_files:
        file_path = os.path.join(folder_path, file_name)
        base_name = os.path.splitext(file_name)[0]
        entity_file_name = f"{base_name}-entity.json"
        entity_file_path = os.path.join(folder_path, entity_file_name)

        if not os.path.exists(entity_file_path):
            print(f"⚠ 跳过 {file_name}：未找到对应的实体文件 {entity_file_name}")
            continue

        print(f"正在处理文件: {file_name} 及 {entity_file_name}")

        try:
            # 1. 读取原始文本文件 (构建 chunk_id -> text 映射)
            with open(file_path, 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
            
            chunk_text_map = {}
            if isinstance(raw_data, list):
                for item in raw_data:
                    if isinstance(item, dict):
                        c_id = item.get("chunk_id")
                        if c_id:
                            chunk_text_map[c_id] = item.get("text", "")

            # 2. 读取实体文件 (按 chunk_id 聚合实体)
            with open(entity_file_path, 'r', encoding='utf-8') as f:
                entities_data = json.load(f)

            group_entities = defaultdict(list)
            if isinstance(entities_data, list):
                for ent in entities_data:
                    if isinstance(ent, dict):
                        c_id = ent.get("chunk_id")
                        if c_id:
                            group_entities[c_id].append(ent)

            # 3. 对每一组送入 LLM 提取关系并组装完整实体数据
            all_extracted_relations = []
            for c_id, ents in group_entities.items():
                if len(ents) < 2:
                    continue
                
                chunk_text = chunk_text_map.get(c_id, "")
                print(f"  └─ 处理组 [{c_id}] (包含 {len(ents)} 个实体)...")
                
                rels = extract_relations_for_group(
                    chunk_id=c_id,
                    chunk_text=chunk_text,
                    entities=ents,
                    model_name=model_name
                )
                all_extracted_relations.extend(rels)

            # 4. 写入输出文件夹的新 JSON 文件
            out_file_path = os.path.join(output_dir, f"{base_name}-relations.json")
            with open(out_file_path, 'w', encoding='utf-8') as out_f:
                json.dump(all_extracted_relations, out_f, ensure_ascii=False, indent=4)

            print(f"✓ 成功生成: {out_file_path} (共提取到 {len(all_extracted_relations)} 条关系)\n")

        except Exception as e:
            print(f"✕ 处理文件 {file_name} 失败: {e}\n")

    print("=" * 50)
    print(f"所有文件的关系提取已完成！结果保存在: {output_dir}")

if __name__ == "__main__":
    folder_path = input("请输入包含 JSON 和实体 JSON 的文件夹路径: ").strip()
    process_folder_relations(folder_path, model_name="deepseek-chat")