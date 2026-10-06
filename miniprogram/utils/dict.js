// 枚举中文映射字典（总纲第 8 章，前端唯一内置字典源）
const ROLE = {
  inspector: '巡查员',
  rectifier: '整改责任人',
  project_admin: '项目管理员',
  org_admin: '组织管理员',
  reviewer: '复核人',
  viewer: '只读',
  admin: '管理员',
};
const SPECIALTY = { civil: '土建', mech: '机电', deco: '装修', safety: '安全' };
const STATUS = { pending: '待接收', accepted: '已接收', processing: '整改中', review: '待审核', closed: '已闭环', cancelled: '已作废' };
const SEVERITY = { '轻微': '轻微', '中度': '中度', '严重': '严重', '无': '无' };
const VERDICT = { normal: '正常', abnormal: '异常' };
const RISK = { high: '高风险', medium: '中风险', low: '低风险' };
const CATEGORY = { structural: '结构变形', protection: '防护缺失', installation: '安装不规范', material: '材料质量', safety: '安全隐患' };
const TYPE = { building: '房建', road: '道路', tunnel: '隧道', landscape: '景观' };
const PHASE = { foundation: '基坑', structure: '主体结构', mep: '机电安装', decoration: '装修' };

// 语义色映射（总纲第 8 章色板）
const STATUS_COLOR = { pending: '#909399', accepted: '#1E5FD0', processing: '#E6A23C', review: '#9C27B0', closed: '#2E7D3A', cancelled: '#B0B3B8' };
const SEVERITY_COLOR = { '轻微': '#E6A23C', '中度': '#E5652A', '严重': '#D32F2F', '无': '#5B6B7D' };
const RISK_COLOR = { high: '#D32F2F', medium: '#E6A23C', low: '#2E7D3A' };
const VERDICT_COLOR = { normal: '#2E7D3A', abnormal: '#E5652A' };

module.exports = {
  ROLE: ROLE,
  SPECIALTY: SPECIALTY,
  STATUS: STATUS,
  SEVERITY: SEVERITY,
  VERDICT: VERDICT,
  RISK: RISK,
  CATEGORY: CATEGORY,
  TYPE: TYPE,
  PHASE: PHASE,
  STATUS_COLOR: STATUS_COLOR,
  SEVERITY_COLOR: SEVERITY_COLOR,
  RISK_COLOR: RISK_COLOR,
  VERDICT_COLOR: VERDICT_COLOR,
};
