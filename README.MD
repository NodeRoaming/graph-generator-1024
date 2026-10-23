# GraphGenerater

GraphGenerater 是一个面向课程教材与课件的知识图谱生成和处理项目。项目覆盖算法（ALGO）、MATLAB 和医学（MED）资料，包含数据预处理、实体与关系抽取、Neo4j 导入、图谱补充以及分层内容推荐。

## 主要流程

1. **准备数据**：将教材、课件等源文件整理到 `rawdata` 下对应领域目录。
2. **抽取实体**：使用 `entity` 中各领域的脚本和 Jinja prompt，从教材（book）及课件（ppt）文本中抽取实体。
3. **抽取关系**：运行 `relationship` 下对应领域脚本，生成实体间关系；`relationship/write-relationship.py` 汇总并写入关系数据。
4. **构建 Neo4j 图谱**：使用 `neo4j` 中的导入脚本写入教材、课件、章节结构和实体。
5. **补充图谱信息**：`section` 用于生成文档章节结构；`prerequisite` 用于生成和处理先修关系；`embedding`、`importance`、`confidence` 提供向量、重要性及置信度相关处理。
6. **生成分层推荐**：按下方顺序运行 `recommend` 脚本，并在最后运行 `write-recommend.py` 写入 Neo4j。

## 目录概览

| 目录 | 用途 |
|---|---|
| `rawdata` | ALGO、MATLAB、MED 的教材与课件原始数据 |
| `entity` | 实体抽取脚本、领域 prompt 和抽取结果 |
| `relationship` | 关系抽取脚本、prompt 和关系写入程序 |
| `neo4j` | 教材、课件及实体数据的 Neo4j 导入 |
| `section` | 基于图谱内容生成和整理 Section 层级 |
| `prerequisite` | 先修关系生成与处理 |
| `embedding` | Chunk、描述和名称等向量生成 |
| `importance`、`confidence` | 图谱重要性与关系置信度处理 |
| `recommend` | Topic 到 Book、Module、Unit、Page、Chunk 的分层推荐 |
| `toolcode` | 关系过滤、题目生成及其他辅助脚本 |

## 分层推荐

在 `recommend` 目录中按顺序运行：

1. `topic-book.py`：每个 Topic 推荐最多 5 本 Book，生成 `topic_book.csv`。
2. `doc-module.py`：在匹配 Book 的 Module 中，为每个 Document 选择 1 个，生成 `doc_module.csv`。
3. `sec-unit.py`：每个 Section 推荐最多 5 个 Unit，生成 `section_unit.csv`。
4. `chapter-page.py`：每个 Chapter 推荐最多 5 个 Page，生成 `chapter_page.csv`。
5. `slide-chunk.py`：每个 Slide 推荐最多 5 个 Chunk，生成 `slide-chunk.csv`。
6. `write-recommend.py`：将结果写入 Neo4j，创建 `RECOMMEND_BOOK`、`RECOMMEND_MODULE`、`RECOMMEND_UNIT`、`RECOMMEND_PAGE`、`RECOMMEND_CHUNK` 关系，并在来源节点保存对应的 `recommend_*` 路径列表。

每一层的候选范围由上一层匹配结果约束。写入程序会替换对应类型的旧推荐关系和属性。

## 配置

脚本通过环境变量读取数据库或模型服务配置，通常可在项目根目录的 `.env` 中设置。Neo4j 常用变量为 `NEO4J_URI`、`NEO4J_USERNAME`、`NEO4J_PASSWORD` 和 `NEO4J_DATABASE`。实体、关系抽取及部分向量/先修处理还需按所用脚本配置相应模型 API 凭据和服务地址。

项目脚本面向不同数据集和阶段，运行前请检查目标脚本中的输入目录、数据库名称及输出文件设置；并确保 Neo4j 服务及脚本所需扩展（例如使用图数据科学算法时的 Neo4j GDS）可用。
