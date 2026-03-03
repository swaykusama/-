# ════════════════════════════════════════════════════════
#   洞察整理大师 app.py 最终版
# ════════════════════════════════════════════════════════

import streamlit as st
import sqlite3
import os
import io
import json
import time
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(BASE_DIR, "data", "insights.db")
FILES_DIR = os.path.join(BASE_DIR, "files")

# ══════════════════════════════════════════
# 数据库
# ══════════════════════════════════════════

def get_db():
  os.makedirs(os.path.join(BASE_DIR, "data"), exist_ok=True)
  conn = sqlite3.connect(DB_PATH)
  conn.row_factory = sqlite3.Row
  return conn

def init_db():
  conn = get_db()
  conn.executescript("""
      CREATE TABLE IF NOT EXISTS documents (
          id            INTEGER PRIMARY KEY AUTOINCREMENT,
          original_name TEXT NOT NULL,
          stored_name   TEXT NOT NULL,
          file_path     TEXT NOT NULL,
          source        TEXT,
          industry      TEXT,
          year          INTEGER,
          created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS insights (
          id             INTEGER PRIMARY KEY AUTOINCREMENT,
          insight_type   TEXT NOT NULL,
          title          TEXT NOT NULL,
          content        TEXT NOT NULL,
          evidence       TEXT,
          source         TEXT,
          industry       TEXT,
          year           INTEGER,
          tags           TEXT,
          age_group      TEXT,
          gender         TEXT,
          city_tier      TEXT,
          lifestyle      TEXT,
          macro_trend    TEXT,
          cultural_shift TEXT,
          created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS insight_supports (
          id           INTEGER PRIMARY KEY AUTOINCREMENT,
          insight_id   INTEGER,
          document_id  INTEGER,
          support_text TEXT,
          source_name  TEXT,
          created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
      );
  """)
  conn.commit()
  try:
      conn.execute("ALTER TABLE insights ADD COLUMN document_id INTEGER")
      conn.commit()
  except Exception:
      pass
  conn.close()

def save_document(file_bytes, original_name, source, industry, year):
  os.makedirs(FILES_DIR, exist_ok=True)
  stored_name = f"{int(time.time())}_{original_name}"
  file_path   = os.path.join(FILES_DIR, stored_name)
  with open(file_path, "wb") as f:
      f.write(file_bytes)
  conn = get_db()
  cur  = conn.execute(
      "INSERT INTO documents (original_name,stored_name,file_path,source,industry,year) VALUES (?,?,?,?,?,?)",
      (original_name, stored_name, file_path, source, industry, year)
  )
  conn.commit()
  doc_id = cur.lastrowid
  conn.close()
  return doc_id

def get_document(doc_id):
  if not doc_id:
      return None
  conn = get_db()
  row  = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
  conn.close()
  return dict(row) if row else None

def get_all_documents():
  conn = get_db()
  rows = conn.execute("SELECT * FROM documents ORDER BY created_at DESC").fetchall()
  conn.close()
  return [dict(r) for r in rows]

def delete_document(doc_id):
  conn = get_db()
  doc  = conn.execute("SELECT file_path FROM documents WHERE id=?", (doc_id,)).fetchone()
  if doc and os.path.exists(doc["file_path"]):
      os.remove(doc["file_path"])
  conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
  conn.execute("UPDATE insights SET document_id=NULL WHERE document_id=?", (doc_id,))
  conn.commit()
  conn.close()

def save_insight(data):
  conn = get_db()
  cur  = conn.execute("""
      INSERT INTO insights
      (insight_type,title,content,evidence,source,industry,year,tags,
       age_group,gender,city_tier,lifestyle,macro_trend,cultural_shift,document_id)
      VALUES
      (:insight_type,:title,:content,:evidence,:source,:industry,:year,:tags,
       :age_group,:gender,:city_tier,:lifestyle,:macro_trend,:cultural_shift,:document_id)
  """, data)
  conn.commit()
  iid = cur.lastrowid
  conn.close()
  return iid

def get_insights(insight_type=None, keyword=None, industry=None):
  conn   = get_db()
  query  = "SELECT * FROM insights WHERE 1=1"
  params = []
  if insight_type:
      query += " AND insight_type=?"
      params.append(insight_type)
  if keyword:
      query += " AND (title LIKE ? OR content LIKE ? OR evidence LIKE ?)"
      params.extend([f"%{keyword}%"] * 3)
  if industry:
      query += " AND industry LIKE ?"
      params.append(f"%{industry}%")
  query += " ORDER BY created_at DESC"
  rows = conn.execute(query, params).fetchall()
  conn.close()
  return [dict(r) for r in rows]

def delete_insight(iid):
  conn = get_db()
  conn.execute("DELETE FROM insights WHERE id=?", (iid,))
  conn.execute("DELETE FROM insight_supports WHERE insight_id=?", (iid,))
  conn.commit()
  conn.close()

def save_support(insight_id, document_id=None, support_text="", source_name=""):
  conn = get_db()
  conn.execute(
      "INSERT INTO insight_supports (insight_id,document_id,support_text,source_name) VALUES (?,?,?,?)",
      (insight_id, document_id, support_text, source_name)
  )
  conn.commit()
  conn.close()

def get_supports(insight_id):
  conn = get_db()
  rows = conn.execute("""
      SELECT s.*, d.original_name, d.file_path
      FROM insight_supports s
      LEFT JOIN documents d ON s.document_id=d.id
      WHERE s.insight_id=? ORDER BY s.created_at
  """, (insight_id,)).fetchall()
  conn.close()
  return [dict(r) for r in rows]

def delete_support(sid):
  conn = get_db()
  conn.execute("DELETE FROM insight_supports WHERE id=?", (sid,))
  conn.commit()
  conn.close()

def get_stats():
  conn     = get_db()
  total    = conn.execute("SELECT COUNT(*) FROM insights").fetchone()[0]
  era      = conn.execute("SELECT COUNT(*) FROM insights WHERE insight_type='era'").fetchone()[0]
  audience = conn.execute("SELECT COUNT(*) FROM insights WHERE insight_type='audience'").fetchone()[0]
  docs     = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
  conn.close()
  return {"total": total, "era": era, "audience": audience, "docs": docs}

# ══════════════════════════════════════════
# 文件解析
# ══════════════════════════════════════════

def parse_docx(b):
  from docx import Document
  doc = Document(io.BytesIO(b))
  return "\n".join([p.text.strip() for p in doc.paragraphs if p.text.strip()])

def parse_pdf(b):
  import fitz
  doc  = fitz.open(stream=b, filetype="pdf")
  text = ""
  for page in doc:
      text += page.get_text()
  return text

def parse_txt(b):
  return b.decode("utf-8", errors="ignore")

# ══════════════════════════════════════════
# AI 提取
# ══════════════════════════════════════════

def ai_extract_insights(text):
  client = OpenAI(
      api_key=os.getenv("DEEPSEEK_API_KEY"),
      base_url="https://api.deepseek.com"
  )
  prompt = f"""
你是一位资深广告策略师。请从以下策略文档中，提取所有有价值的时代洞察和人群洞察。

文档内容：
---
{text[:4000]}
---

请以JSON格式返回：
{{
"insights": [
  {{
    "insight_type": "era 或 audience",
    "title": "洞察标题，一句话，15字以内，要有穿透力",
    "content": "洞察详细描述，2-3句话",
    "evidence": "支撑依据（如有）",
    "tags": ["标签1", "标签2", "标签3"],
    "age_group": "年龄群（仅人群洞察填）",
    "gender": "性别（仅人群洞察填）",
    "city_tier": "城市层级（仅人群洞察填）",
    "lifestyle": "生活方式（仅人群洞察填）",
    "macro_trend": "宏观趋势（仅时代洞察填）",
    "cultural_shift": "文化转变（仅时代洞察填）"
  }}
]
}}
只返回JSON，不要其他文字。
"""
  resp   = client.chat.completions.create(
      model="deepseek-chat",
      messages=[{"role": "user", "content": prompt}],
      response_format={"type": "json_object"},
      temperature=0.3
  )
  result = json.loads(resp.choices[0].message.content)
  return result.get("insights", [])

# ══════════════════════════════════════════
# 组件：佐证展示与添加
# ══════════════════════════════════════════

def render_supports(insight_id):
  supports = get_supports(insight_id)
  if supports:
      st.markdown("**📎 已有佐证资料**")
      for sup in supports:
          c1, c2 = st.columns([10, 1])
          with c1:
              if sup.get("original_name"):
                  fp = sup["file_path"]
                  if os.path.exists(fp):
                      with open(fp, "rb") as f:
                          fb = f.read()
                      st.download_button(
                          f"📄 {sup['original_name']}",
                          data=fb, file_name=sup["original_name"],
                          key=f"dl_sup_{sup['id']}"
                      )
                  else:
                      st.caption(f"📄 {sup['original_name']}（文件已移动）")
              if sup.get("support_text"):
                  st.caption(f"💬 {sup['support_text']}")
              if sup.get("source_name"):
                  st.caption(f"来源：{sup['source_name']}")
          with c2:
              if st.button("✕", key=f"del_sup_{sup['id']}"):
                  delete_support(sup["id"])
                  st.rerun()

  with st.expander("➕ 添加佐证资料"):
      tf, tt = st.tabs(["📁 上传文件", "💬 文字说明"])
      with tf:
          sf  = st.file_uploader(
              "上传佐证文件", type=["docx","pdf","txt"],
              key=f"sf_{insight_id}", label_visibility="collapsed"
          )
          src = st.text_input("来源说明", key=f"sfsrc_{insight_id}",
                              placeholder="例：尼尔森2024报告")
          if st.button("保存文件佐证", key=f"savesf_{insight_id}") and sf:
              did = save_document(sf.read(), sf.name, src, "", datetime.now().year)
              save_support(insight_id, document_id=did, source_name=src)
              st.success("✅ 已保存！")
              st.rerun()
      with tt:
          stxt = st.text_area(
              "文字佐证", key=f"stxt_{insight_id}",
              placeholder="例：QuestMobile数据显示，Z世代日均刷短视频3.2小时..."
          )
          ssrc = st.text_input("来源", key=f"ssrc_{insight_id}",
                               placeholder="例：QuestMobile 2024")
          if st.button("保存文字佐证", key=f"savest_{insight_id}") and stxt:
              save_support(insight_id, support_text=stxt, source_name=ssrc)
              st.success("✅ 已保存！")
              st.rerun()

# ══════════════════════════════════════════
# 组件：洞察录入表单（供多处复用）
# ══════════════════════════════════════════

def render_insight_form(form_key, doc_id=None):
  """
  渲染洞察录入表单，返回 (是否提交成功, 新洞察的ID)
  """
  with st.form(form_key, clear_on_submit=True):
      c1, c2 = st.columns(2)
      with c1:
          itype = st.selectbox(
              "洞察类型 *", ["era", "audience"],
              format_func=lambda x: "🌐 时代洞察" if x == "era" else "👥 人群洞察"
          )
      with c2:
          year = st.number_input("年份", min_value=2000, max_value=2035,
                                 value=datetime.now().year)
      title    = st.text_input("洞察标题 *", placeholder="一句话，要有张力")
      content  = st.text_area("洞察描述 *", height=100,
                              placeholder="展开说明，2-3句话...")
      evidence = st.text_area("支撑依据", height=70,
                              placeholder="数据、研究、现象（没有可不填）")
      c3, c4 = st.columns(2)
      with c3:
          source   = st.text_input("来源 / 品牌", placeholder="例：某品牌提案")
      with c4:
          industry = st.text_input("所属行业",    placeholder="例：美妆 / 快消")
      tags_input = st.text_input("标签（逗号分隔）",
                                 placeholder="例：Z世代, 情绪消费, 悦己")
      st.divider()
      if itype == "audience":
          st.markdown("**👥 人群维度**")
          c5, c6 = st.columns(2)
          with c5:
              age_group = st.selectbox("年龄群体", [
                  "", "Z世代 (1995-2009)", "千禧一代 (1980-1995)",
                  "X世代 (1965-1980)", "银发族 (1965前)", "小镇青年", "其他"
              ])
              city_tier = st.selectbox("城市层级", [
                  "", "一线城市", "新一线城市", "二线城市", "下沉市场", "全线通用"
              ])
          with c6:
              gender    = st.selectbox("性别", ["", "不限", "女性为主", "男性为主"])
              lifestyle = st.text_input("生活方式", placeholder="例：精致懒")
          macro_trend = cultural_shift = ""
      else:
          st.markdown("**🌐 时代维度**")
          c5, c6 = st.columns(2)
          with c5:
              macro_trend    = st.text_input("宏观趋势",
                                             placeholder="例：情绪消费")
          with c6:
              cultural_shift = st.text_input("文化转变",
                                             placeholder="例：从炫耀消费转向悦己消费")
          age_group = gender = city_tier = lifestyle = ""

      submitted = st.form_submit_button("💾 保存这条洞察", type="primary",
                                        use_container_width=True)
      if submitted:
          if not title or not content:
              st.error("❗ 标题和描述是必填项！")
              return False, None
          tags_list = [t.strip() for t in tags_input.split(",") if t.strip()]
          iid = save_insight({
              "insight_type": itype, "title": title, "content": content,
              "evidence": evidence, "source": source, "industry": industry,
              "year": year, "tags": json.dumps(tags_list, ensure_ascii=False),
              "age_group": age_group, "gender": gender, "city_tier": city_tier,
              "lifestyle": lifestyle, "macro_trend": macro_trend,
              "cultural_shift": cultural_shift, "document_id": doc_id
          })
          return True, iid
  return False, None

# ══════════════════════════════════════════
# 组件：洞察卡片
# ══════════════════════════════════════════

def render_insight_card(ins):
  icon  = "🌐" if ins["insight_type"] == "era" else "👥"
  cc, cd = st.columns([11, 1])
  with cc:
      with st.expander(f"{icon} **{ins['title']}**"):
          st.write(ins["content"])
          if ins.get("evidence"):
              st.info(f"📊 **依据：** {ins['evidence']}")
          dims = {}
          if ins["insight_type"] == "audience":
              if ins.get("age_group"):      dims["年龄群体"] = ins["age_group"]
              if ins.get("gender"):         dims["性别"]     = ins["gender"]
              if ins.get("city_tier"):      dims["城市层级"] = ins["city_tier"]
              if ins.get("lifestyle"):      dims["生活方式"] = ins["lifestyle"]
          else:
              if ins.get("macro_trend"):    dims["宏观趋势"] = ins["macro_trend"]
              if ins.get("cultural_shift"): dims["文化转变"] = ins["cultural_shift"]
          if dims:
              dc = st.columns(len(dims))
              for i, (k, v) in enumerate(dims.items()):
                  dc[i].metric(k, v)
          if ins.get("tags"):
              try:
                  tgs = json.loads(ins["tags"])
                  if tgs:
                      st.write("🏷️ " + "  ".join([f"`{t}`" for t in tgs]))
              except Exception:
                  pass
          st.divider()
          if ins.get("document_id"):
              doc = get_document(ins["document_id"])
              if doc and os.path.exists(doc["file_path"]):
                  with open(doc["file_path"], "rb") as f:
                      fb = f.read()
                  st.download_button(
                      f"📄 来源文件：{doc['original_name']}",
                      data=fb, file_name=doc["original_name"],
                      key=f"dl_{ins['id']}"
                  )
          st.caption(
              f"来源：{ins.get('source') or '未标注'} ｜ "
              f"行业：{ins.get('industry') or '未标注'} ｜ "
              f"{str(ins.get('created_at',''))[:10]}"
          )
          st.divider()
          render_supports(ins["id"])
  with cd:
      if st.button("🗑️", key=f"del_{ins['id']}"):
          delete_insight(ins["id"])
          st.rerun()

# ══════════════════════════════════════════
# 页面：首页
# ══════════════════════════════════════════

def page_home():
  st.title("🧠 洞察整理大师")
  st.caption("广告策略人的洞察数据库")
  st.divider()
  stats = get_stats()
  c1, c2, c3, c4 = st.columns(4)
  c1.metric("📦 洞察总数", f"{stats['total']} 条")
  c2.metric("🌐 时代洞察", f"{stats['era']} 条")
  c3.metric("👥 人群洞察", f"{stats['audience']} 条")
  c4.metric("📄 文档数量", f"{stats['docs']} 份")
  st.divider()
  st.subheader("📝 最近录入")
  recent = get_insights()[:5]
  if not recent:
      st.info("💡 还没有任何洞察，点击左边菜单添加第一条吧！")
  else:
      for ins in recent:
          icon = "🌐" if ins["insight_type"] == "era" else "👥"
          with st.expander(f"{icon} {ins['title']}"):
              st.write(ins["content"])
              if ins.get("document_id"):
                  doc = get_document(ins["document_id"])
                  if doc and os.path.exists(doc["file_path"]):
                      with open(doc["file_path"], "rb") as f:
                          fb = f.read()
                      st.download_button(
                          f"📄 来源文件：{doc['original_name']}",
                          data=fb, file_name=doc["original_name"],
                          key=f"hdl_{ins['id']}"
                      )
              st.caption(
                  f"来源：{ins.get('source') or '未标注'} ｜ "
                  f"{str(ins.get('created_at',''))[:10]}"
              )

# ══════════════════════════════════════════
# 页面：手动录入
# 需求1：保存后立即出现佐证文件上传区域
# ══════════════════════════════════════════

def page_manual():
  st.title("✍️ 手动录入洞察")
  st.caption("把你脑子里的洞察直接录入数据库")

  # ── Phase 2：保存成功后，显示佐证上传区域 ──
  if "last_manual_iid" in st.session_state:
      iid  = st.session_state["last_manual_iid"]
      conn = get_db()
      row  = conn.execute(
          "SELECT title FROM insights WHERE id=?", (iid,)
      ).fetchone()
      conn.close()

      if row:
          st.success(f"✅ 洞察「{row['title']}」已保存！")
          st.subheader("📎 为这条洞察上传佐证文件（可选）")
          render_supports(iid)
          st.divider()
          if st.button("✅ 完成，录入下一条洞察",
                       type="primary", use_container_width=True):
              del st.session_state["last_manual_iid"]
              st.rerun()
      return  # 不再显示录入表单

  # ── Phase 1：显示录入表单 ──────────────────
  submitted, iid = render_insight_form("manual_main_form")
  if submitted and iid:
      st.session_state["last_manual_iid"] = iid
      st.rerun()  # 立刻跳转到 Phase 2

# ══════════════════════════════════════════
# 页面：洞察库
# 需求2：顶部可以直接添加新洞察
# ══════════════════════════════════════════

def page_browse():
  st.title("🗂️ 洞察库")

  # ── 顶部：快速跳转到手动录入（需求2）────────
  col_title, col_btn = st.columns([8, 2])
  with col_btn:
      if st.button("✍️ 添加新洞察", use_container_width=True):
          # 清除上次保存的 ID，避免直接跳到佐证界面
          st.session_state.pop("last_manual_iid", None)
          st.session_state["nav_to"] = "✍️ 手动录入"
          st.rerun()

  # ── 搜索筛选 ──────────────────────────────
  c1, c2 = st.columns(2)
  with c1:
      kw  = st.text_input("🔍 关键词", placeholder="搜索标题 / 内容 / 依据...")
  with c2:
      ind = st.text_input("行业筛选", placeholder="例：美妆 / 快消")

  st.divider()

  # ── 两个 Tab：时代洞察 / 人群洞察 ─────────
  tab_era, tab_audience = st.tabs(["🌐 时代洞察", "👥 人群洞察"])

  with tab_era:
      era_list = get_insights(
          insight_type="era",
          keyword=kw  or None,
          industry=ind or None
      )
      st.caption(f"共 **{len(era_list)}** 条时代洞察")
      if not era_list:
          st.info("暂无时代洞察，点击右上角「✍️ 添加新洞察」")
      else:
          for ins in era_list:
              render_insight_card(ins)

  with tab_audience:
      aud_list = get_insights(
          insight_type="audience",
          keyword=kw  or None,
          industry=ind or None
      )
      st.caption(f"共 **{len(aud_list)}** 条人群洞察")
      if not aud_list:
          st.info("暂无人群洞察，点击右上角「✍️ 添加新洞察」")
      else:
          for ins in aud_list:
              render_insight_card(ins)

# ══════════════════════════════════════════
# 页面：文档库
# ══════════════════════════════════════════

def page_docs():
  st.title("📁 文档库")
  st.caption("管理所有上传的文档")

  with st.expander("➕ 上传新文档"):
      c1, c2 = st.columns(2)
      with c1:
          new_src = st.text_input("来源 / 品牌", key="new_doc_src",
                                  placeholder="例：某品牌提案")
      with c2:
          new_ind = st.text_input("所属行业", key="new_doc_ind",
                                  placeholder="例：美妆 / 快消")
      new_file = st.file_uploader("选择文件", type=["docx","pdf","txt"],
                                  key="new_doc_file")
      if st.button("📤 上传保存", type="primary") and new_file:
          save_document(new_file.read(), new_file.name,
                        new_src, new_ind, datetime.now().year)
          st.success(f"✅ 已上传：{new_file.name}")
          st.rerun()

  st.divider()
  docs = get_all_documents()
  if not docs:
      st.info("还没有上传任何文档，去「🤖 AI 导入」上传第一份吧！")
      return

  st.caption(f"共 **{len(docs)}** 份文档")
  for doc in docs:
      col_card, col_del = st.columns([11, 1])
      with col_card:
          with st.expander(f"📄 {doc['original_name']}"):
              ca, cb, cc = st.columns(3)
              ca.metric("来源",     doc.get("source")   or "未标注")
              cb.metric("行业",     doc.get("industry") or "未标注")
              cc.metric("上传时间", str(doc.get("created_at",""))[:10])
              conn  = get_db()
              count = conn.execute(
                  "SELECT COUNT(*) FROM insights WHERE document_id=?",
                  (doc["id"],)
              ).fetchone()[0]
              conn.close()
              st.caption(f"🔗 已关联 **{count}** 条洞察")
              if os.path.exists(doc["file_path"]):
                  with open(doc["file_path"], "rb") as f:
                      fb = f.read()
                  st.download_button(
                      "📥 下载原文件", data=fb,
                      file_name=doc["original_name"],
                      key=f"doc_dl_{doc['id']}"
                  )
              else:
                  st.warning("⚠️ 文件已从磁盘移除")
      with col_del:
          if st.button("🗑️", key=f"doc_del_{doc['id']}", help="删除此文档"):
              delete_document(doc["id"])
              st.rerun()

# ══════════════════════════════════════════
# 页面：AI 导入
# 需求3：保存后可手动补充 AI 遗漏的洞察
# ══════════════════════════════════════════

def page_ai_import():
  st.title("🤖 AI 智能导入")
  st.caption("上传策略文档，AI 自动提取洞察，可手动补充遗漏部分")

  if not os.getenv("DEEPSEEK_API_KEY"):
      st.error("⚠️ 未找到 API Key，请检查 .env 文件")
      return

  # ── 如果已完成 AI 保存，显示手动补充界面（需求3）──
  if st.session_state.get("ai_import_done"):
      doc_id = st.session_state.get("ai_saved_doc_id")
      st.success("✅ AI 洞察已保存！如有遗漏，可在下方手动补充")

      st.subheader("✏️ AI 有遗漏？手动补充一条")
      st.caption("补充的洞察会自动关联同一份原始文档")

      submitted, iid = render_insight_form("ai_supplement_form", doc_id=doc_id)
      if submitted and iid:
          st.success("✅ 补充洞察已保存！")
          st.rerun()

      st.divider()
      if st.button("🔄 导入新文档", use_container_width=True):
          st.session_state.pop("ai_import_done", None)
          st.session_state.pop("ai_saved_doc_id", None)
          st.rerun()
      return

  # ── 正常 AI 导入流程 ─────────────────────────
  c1, c2 = st.columns(2)
  with c1:
      source   = st.text_input("文档来源 / 品牌 *",
                                placeholder="例：某运动品牌2025策略提案")
  with c2:
      industry = st.text_input("所属行业", placeholder="例：运动 / 快消 / 科技")

  st.divider()
  tab1, tab2 = st.tabs(["📁 上传文件", "📋 粘贴文字"])
  text = ""
  file_info = None

  with tab1:
      st.caption("支持 Word (.docx) · PDF (.pdf) · 纯文本 (.txt)")
      up = st.file_uploader("选择文件", type=["docx","pdf","txt"],
                            label_visibility="collapsed")
      if up:
          fb = up.read()
          file_info = {"bytes": fb, "name": up.name}
          with st.spinner("正在读取文件..."):
              try:
                  if up.name.endswith(".docx"):
                      text = parse_docx(fb)
                  elif up.name.endswith(".pdf"):
                      text = parse_pdf(fb)
                  else:
                      text = parse_txt(fb)
                  st.success(f"✅ 已读取：{up.name}（共 {len(text)} 字）")
                  with st.expander("📄 文档内容预览"):
                      st.text(text[:800] + "..." if len(text) > 800 else text)
              except Exception as e:
                  st.error(f"文件读取失败：{e}")

  with tab2:
      pasted = st.text_area("粘贴文字内容", height=250,
                            placeholder="把提案中洞察部分粘贴进来...")
      if pasted:
          text = pasted

  st.divider()
  if not source:
      st.warning("⬆️ 请先填写「文档来源 / 品牌」")

  if st.button("🚀 开始 AI 提取洞察", type="primary",
               disabled=not (text and source), use_container_width=True):
      with st.spinner("🤖 AI 正在分析，通常需要 10-30 秒..."):
          try:
              ins = ai_extract_insights(text)
              st.session_state["ai_results"]   = ins
              st.session_state["ai_source"]    = source
              st.session_state["ai_industry"]  = industry
              st.session_state["ai_file_info"] = file_info
          except Exception as e:
              st.error(f"提取失败：{e}")

  if st.session_state.get("ai_results"):
      results = st.session_state["ai_results"]
      st.divider()
      st.success(f"🎉 AI 共提取到 {len(results)} 条洞察，请勾选后保存")

      selected = []
      for i, ins in enumerate(results):
          icon  = "🌐" if ins["insight_type"] == "era" else "👥"
          label = "时代洞察" if ins["insight_type"] == "era" else "人群洞察"
          ck, ct = st.columns([1, 11])
          with ck:
              checked = st.checkbox("", value=True, key=f"chk_{i}")
          with ct:
              st.markdown(f"**{icon} {ins['title']}** `{label}`")
              st.caption(ins["content"])
              if ins.get("evidence"):
                  st.caption(f"📊 {ins['evidence']}")
          if checked:
              selected.append(ins)
          st.write("")

      st.divider()
      cs, cc2 = st.columns([3, 1])
      with cs:
          if st.button(f"💾 保存选中的 {len(selected)} 条", type="primary",
                       disabled=len(selected) == 0, use_container_width=True):
              doc_id = None
              fi = st.session_state.get("ai_file_info")
              if fi:
                  doc_id = save_document(
                      fi["bytes"], fi["name"],
                      st.session_state["ai_source"],
                      st.session_state["ai_industry"],
                      datetime.now().year
                  )
              for ins in selected:
                  save_insight({
                      "insight_type": ins["insight_type"],
                      "title":        ins["title"],
                      "content":      ins["content"],
                      "evidence":     ins.get("evidence", ""),
                      "source":       st.session_state["ai_source"],
                      "industry":     st.session_state["ai_industry"],
                      "year":         datetime.now().year,
                      "tags":         json.dumps(ins.get("tags",[]),
                                                 ensure_ascii=False),
                      "age_group":    ins.get("age_group",""),
                      "gender":       ins.get("gender",""),
                      "city_tier":    ins.get("city_tier",""),
                      "lifestyle":    ins.get("lifestyle",""),
                      "macro_trend":  ins.get("macro_trend",""),
                      "cultural_shift": ins.get("cultural_shift",""),
                      "document_id":  doc_id
                  })
              # 清理并进入补充模式
              del st.session_state["ai_results"]
              st.session_state.pop("ai_file_info", None)
              st.session_state["ai_import_done"]  = True
              st.session_state["ai_saved_doc_id"] = doc_id
              st.rerun()

      with cc2:
          if st.button("✖️ 重新来", use_container_width=True):
              del st.session_state["ai_results"]
              st.rerun()

# ══════════════════════════════════════════
# 主程序
# ══════════════════════════════════════════

def main():
  st.set_page_config(
      page_title="洞察整理大师", page_icon="🧠",
      layout="wide", initial_sidebar_state="expanded"
  )
  init_db()

  pages = ["🏠 首页", "✍️ 手动录入", "🗂️ 洞察库", "📁 文档库", "🤖 AI 导入"]

  # 处理页面跳转请求（如从洞察库点「添加新洞察」）
  if "nav_to" in st.session_state:
      default = st.session_state.pop("nav_to")
  else:
      default = "🏠 首页"

  idx = pages.index(default) if default in pages else 0

  with st.sidebar:
      st.title("🧠 洞察整理大师")
      st.caption("广告策略人的洞察数据库")
      st.divider()
      page = st.radio(
          "导航菜单", pages,
          index=idx,
          label_visibility="collapsed"
      )
      st.divider()
      stats = get_stats()
      st.caption(f"📦 共 **{stats['total']}** 条洞察")
      st.caption(f"🌐 时代 {stats['era']} 条 ｜ 👥 人群 {stats['audience']} 条")
      st.caption(f"📄 已上传 **{stats['docs']}** 份文档")

  if page == "🏠 首页":
      page_home()
  elif page == "✍️ 手动录入":
      page_manual()
  elif page == "🗂️ 洞察库":
      page_browse()
  elif page == "📁 文档库":
      page_docs()
  elif page == "🤖 AI 导入":
      page_ai_import()

if __name__ == "__main__":
  main()