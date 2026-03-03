# ════════════════════════════════════════════════════════
#   洞察整理大师 app.py 云端修复版
# ════════════════════════════════════════════════════════

import streamlit as st
import os
import io
import json
import time
import requests
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()

# ══════════════════════════════════════════
# 初始化
# ══════════════════════════════════════════

def get_secret(key):
  try:
      return st.secrets[key]
  except Exception:
      return os.getenv(key)

@st.cache_resource
def get_supabase():
  return create_client(
      get_secret("SUPABASE_URL"),
      get_secret("SUPABASE_KEY")
  )

# ══════════════════════════════════════════
# 文档操作
# ══════════════════════════════════════════

def save_document(file_bytes, original_name, source, industry, year):
  sb          = get_supabase()
  stored_name = f"{int(time.time())}_{original_name}"
  file_url    = ""
  try:
      sb.storage.from_("documents").upload(stored_name, file_bytes)
      file_url = sb.storage.from_("documents").get_public_url(stored_name)
  except Exception as e:
      st.warning(f"文件上传失败：{e}")
  result = sb.table("documents").insert({
      "original_name": original_name,
      "stored_name":   stored_name,
      "file_url":      file_url,
      "source":        source,
      "industry":      industry,
      "year":          year
  }).execute()
  return result.data[0]["id"] if result.data else None

def get_document(doc_id):
  if not doc_id:
      return None
  result = get_supabase().table("documents").select("*").eq("id", doc_id).execute()
  return result.data[0] if result.data else None

def get_all_documents():
  return get_supabase().table("documents").select("*").order(
      "created_at", desc=True).execute().data or []

def delete_document(doc_id):
  sb  = get_supabase()
  doc = get_document(doc_id)
  if doc and doc.get("stored_name"):
      try:
          sb.storage.from_("documents").remove([doc["stored_name"]])
      except Exception:
          pass
  sb.table("documents").delete().eq("id", doc_id).execute()
  sb.table("insights").update({"document_id": None}).eq("document_id", doc_id).execute()

def get_file_bytes(stored_name, file_url=None):
  """先从 Storage 下载，失败则用公开 URL"""
  if stored_name:
      try:
          return get_supabase().storage.from_("documents").download(stored_name)
      except Exception:
          pass
  if file_url:
      try:
          r = requests.get(file_url, timeout=15)
          if r.status_code == 200:
              return r.content
      except Exception:
          pass
  return None

# ══════════════════════════════════════════
# 洞察操作
# ══════════════════════════════════════════

def save_insight(data):
  result = get_supabase().table("insights").insert(data).execute()
  return result.data[0]["id"] if result.data else None

def get_insights(insight_type=None, keyword=None, industry=None):
  sb    = get_supabase()
  query = sb.table("insights").select("*")
  if insight_type:
      query = query.eq("insight_type", insight_type)
  if keyword:
      query = query.or_(
          f"title.ilike.%{keyword}%,"
          f"content.ilike.%{keyword}%,"
          f"evidence.ilike.%{keyword}%"
      )
  if industry:
      query = query.ilike("industry", f"%{industry}%")
  return query.order("created_at", desc=True).execute().data or []

def delete_insight(iid):
  sb = get_supabase()
  sb.table("insight_supports").delete().eq("insight_id", iid).execute()
  sb.table("insights").delete().eq("id", iid).execute()

# ══════════════════════════════════════════
# 佐证操作
# ══════════════════════════════════════════

def save_support(insight_id, document_id=None, support_text="", source_name=""):
  get_supabase().table("insight_supports").insert({
      "insight_id":   insight_id,
      "document_id":  document_id,
      "support_text": support_text,
      "source_name":  source_name
  }).execute()

def get_supports(insight_id):
  """分两步查询，避免联表报错"""
  sb     = get_supabase()
  result = sb.table("insight_supports").select("*").eq(
      "insight_id", insight_id
  ).execute()

  supports = []
  for row in (result.data or []):
      row["original_name"] = None
      row["stored_name"]   = None
      row["file_url"]      = None
      if row.get("document_id"):
          doc_res = sb.table("documents").select(
              "original_name, stored_name, file_url"
          ).eq("id", row["document_id"]).execute()
          if doc_res.data:
              d = doc_res.data[0]
              row["original_name"] = d.get("original_name")
              row["stored_name"]   = d.get("stored_name")
              row["file_url"]      = d.get("file_url")
      supports.append(row)
  return supports

def delete_support(sid):
  get_supabase().table("insight_supports").delete().eq("id", sid).execute()

def get_stats():
  sb      = get_supabase()
  all_ins = sb.table("insights").select("insight_type").execute().data or []
  era     = sum(1 for i in all_ins if i["insight_type"] == "era")
  aud     = sum(1 for i in all_ins if i["insight_type"] == "audience")
  docs    = len(sb.table("documents").select("id").execute().data or [])
  return {"total": len(all_ins), "era": era, "audience": aud, "docs": docs}

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
      api_key=get_secret("DEEPSEEK_API_KEY"),
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
  return json.loads(resp.choices[0].message.content).get("insights", [])

# ══════════════════════════════════════════
# 组件：佐证
# ══════════════════════════════════════════

def render_supports(insight_id):
  supports = get_supports(insight_id)
  if supports:
      st.markdown("**📎 已有佐证资料**")
      for sup in supports:
          c1, c2 = st.columns([10, 1])
          with c1:
              if sup.get("original_name"):
                  fb = get_file_bytes(
                      sup.get("stored_name"),
                      sup.get("file_url")
                  )
                  if fb:
                      st.download_button(
                          f"📄 {sup['original_name']}",
                          data=fb,
                          file_name=sup["original_name"],
                          key=f"dl_sup_{sup['id']}"
                      )
                  elif sup.get("file_url"):
                      st.markdown(f"[📄 {sup['original_name']}]({sup['file_url']})")
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
          stxt = st.text_area("文字佐证", key=f"stxt_{insight_id}",
                              placeholder="例：QuestMobile数据显示...")
          ssrc = st.text_input("来源", key=f"ssrc_{insight_id}",
                               placeholder="例：QuestMobile 2024")
          if st.button("保存文字佐证", key=f"savest_{insight_id}") and stxt:
              save_support(insight_id, support_text=stxt, source_name=ssrc)
              st.success("✅ 已保存！")
              st.rerun()

# ══════════════════════════════════════════
# 组件：洞察录入表单
# ══════════════════════════════════════════

def render_insight_form(form_key, doc_id=None):
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
                                 placeholder="例：Z世代, 情绪消费")
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
                                             placeholder="例：从炫耀转向悦己")
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
              if doc:
                  fb = get_file_bytes(
                      doc.get("stored_name"),
                      doc.get("file_url")
                  )
                  if fb:
                      st.download_button(
                          f"📄 来源文件：{doc['original_name']}",
                          data=fb, file_name=doc["original_name"],
                          key=f"dl_{ins['id']}"
                      )
                  elif doc.get("file_url"):
                      st.markdown(f"[📄 来源文件：{doc['original_name']}]({doc['file_url']})")
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
                  if doc:
                      fb = get_file_bytes(
                          doc.get("stored_name"),
                          doc.get("file_url")
                      )
                      if fb:
                          st.download_button(
                              f"📄 来源文件：{doc['original_name']}",
                              data=fb, file_name=doc["original_name"],
                              key=f"hdl_{ins['id']}"
                          )
                      elif doc.get("file_url"):
                          st.markdown(f"[📄 {doc['original_name']}]({doc['file_url']})")
              st.caption(
                  f"来源：{ins.get('source') or '未标注'} ｜ "
                  f"{str(ins.get('created_at',''))[:10]}"
              )

# ══════════════════════════════════════════
# 页面：手动录入
# ══════════════════════════════════════════

def page_manual():
  st.title("✍️ 手动录入洞察")
  st.caption("把你脑子里的洞察直接录入数据库")

  if "last_manual_iid" in st.session_state:
      iid  = st.session_state["last_manual_iid"]
      rows = get_supabase().table("insights").select("title").eq(
          "id", iid).execute().data
      if rows:
          st.success(f"✅ 洞察「{rows[0]['title']}」已保存！")
          st.subheader("📎 为这条洞察上传佐证文件（可选）")
          render_supports(iid)
          st.divider()
          if st.button("✅ 完成，录入下一条洞察",
                       type="primary", use_container_width=True):
              del st.session_state["last_manual_iid"]
              st.rerun()
      return

  submitted, iid = render_insight_form("manual_main_form")
  if submitted and iid:
      st.session_state["last_manual_iid"] = iid
      st.rerun()

# ══════════════════════════════════════════
# 页面：洞察库
# ══════════════════════════════════════════

def page_browse():
  st.title("🗂️ 洞察库")

  col_title, col_btn = st.columns([8, 2])
  with col_btn:
      if st.button("✍️ 添加新洞察", use_container_width=True):
          st.session_state.pop("last_manual_iid", None)
          st.session_state["nav_to"] = "✍️ 手动录入"
          st.rerun()

  c1, c2 = st.columns(2)
  with c1:
      kw  = st.text_input("🔍 关键词", placeholder="搜索标题 / 内容 / 依据...")
  with c2:
      ind = st.text_input("行业筛选", placeholder="例：美妆 / 快消")

  st.divider()

  tab_era, tab_audience = st.tabs(["🌐 时代洞察", "👥 人群洞察"])

  with tab_era:
      era_list = get_insights(insight_type="era",
                              keyword=kw  or None,
                              industry=ind or None)
      st.caption(f"共 **{len(era_list)}** 条时代洞察")
      if not era_list:
          st.info("暂无时代洞察，点击右上角「✍️ 添加新洞察」")
      else:
          for ins in era_list:
              render_insight_card(ins)

  with tab_audience:
      aud_list = get_insights(insight_type="audience",
                              keyword=kw  or None,
                              industry=ind or None)
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
              count = len(get_supabase().table("insights").select("id").eq(
                  "document_id", doc["id"]).execute().data or [])
              st.caption(f"🔗 已关联 **{count}** 条洞察")
              fb = get_file_bytes(doc.get("stored_name"), doc.get("file_url"))
              if fb:
                  st.download_button(
                      "📥 下载原文件", data=fb,
                      file_name=doc["original_name"],
                      key=f"doc_dl_{doc['id']}"
                  )
              elif doc.get("file_url"):
                  st.markdown(f"[📥 查看原文件]({doc['file_url']})")
              else:
                  st.warning("⚠️ 文件暂时无法下载")
      with col_del:
          if st.button("🗑️", key=f"doc_del_{doc['id']}", help="删除此文档"):
              delete_document(doc["id"])
              st.rerun()

# ══════════════════════════════════════════
# 页面：AI 导入
# ══════════════════════════════════════════

def page_ai_import():
  st.title("🤖 AI 智能导入")
  st.caption("上传策略文档，AI 自动提取洞察，可手动补充遗漏部分")

  if not get_secret("DEEPSEEK_API_KEY"):
      st.error("⚠️ 未找到 API Key")
      return

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

  c1, c2 = st.columns(2)
  with c1:
      source   = st.text_input("文档来源 / 品牌 *",
                                placeholder="例：某运动品牌2025策略提案")
  with c2:
      industry = st.text_input("所属行业", placeholder="例：运动 / 快消 / 科技")

  st.divider()
  tab1, tab2 = st.tabs(["📁 上传文件", "📋 粘贴文字"])
  text      = ""
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
                  if up.name.endswith(".docx"):   text = parse_docx(fb)
                  elif up.name.endswith(".pdf"):  text = parse_pdf(fb)
                  else:                           text = parse_txt(fb)
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

  pages = ["🏠 首页", "✍️ 手动录入", "🗂️ 洞察库", "📁 文档库", "🤖 AI 导入"]

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
          "导航菜单", pages, index=idx,
          label_visibility="collapsed"
      )
      st.divider()
      try:
          stats = get_stats()
          st.caption(f"📦 共 **{stats['total']}** 条洞察")
          st.caption(f"🌐 时代 {stats['era']} 条 ｜ 👥 人群 {stats['audience']} 条")
          st.caption(f"📄 已上传 **{stats['docs']}** 份文档")
      except Exception:
          st.caption("⚠️ 数据库连接中...")

  if page == "🏠 首页":        page_home()
  elif page == "✍️ 手动录入": page_manual()
  elif page == "🗂️ 洞察库":   page_browse()
  elif page == "📁 文档库":   page_docs()
  elif page == "🤖 AI 导入":  page_ai_import()

if __name__ == "__main__":
  main()