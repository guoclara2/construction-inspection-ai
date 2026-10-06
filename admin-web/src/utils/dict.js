// 枚举中文映射字典（总纲第 8 章，B端唯一内置字典源）
export const ROLE = {
  inspector: '巡查员', rectifier: '整改责任人', reviewer: '复核人',
  project_admin: '项目管理员', viewer: '只读', org_admin: '组织管理员',
};
// 成员角色可选项（新增/编辑成员下拉）：按需求移除「只读、复核人」，
// 仅保留三类核心角色；ROLE 完整映射仍用于存量成员标签展示。
export const MEMBER_ROLE_OPTIONS = {
  inspector: '巡查员', rectifier: '整改责任人', project_admin: '项目管理员',
};
export const SPECIALTY = { civil: '土建', mech: '机电', deco: '装修', safety: '安全' };
export const STATUS = { pending: '待接收', accepted: '已接收', processing: '整改中', review: '待审核', closed: '已闭环', cancelled: '已作废' };
export const VERDICT = { normal: '正常', abnormal: '异常' };
export const RISK = { high: '高', medium: '中', low: '低' };
export const CATEGORY = { structural: '结构变形', protection: '防护缺失', installation: '安装不规范', material: '材料质量', safety: '安全隐患' };
export const TYPE = { building: '房建', road: '道路', tunnel: '隧道', landscape: '景观' };
export const PHASE = { foundation: '基坑', structure: '主体结构', mep: '机电安装', decoration: '装修' };

// 工单状态 → el-tag 类型（总纲第 8 章）
export const STATUS_TAG = { pending: 'info', accepted: 'primary', processing: 'warning', review: 'danger', closed: 'success', cancelled: 'info' };

export function fmtTime(str) {
  if (!str) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(str));
  return m ? `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}` : str;
}
