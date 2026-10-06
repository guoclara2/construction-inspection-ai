<template>
  <el-card shadow="never">
    <template #header>
      <div class="card-head">
        <span>检查项知识库</span>
        <div>
          <el-button @click="downloadTemplate">下载模板</el-button>
          <el-button @click="importDialog.visible = true">批量导入</el-button>
          <el-button type="primary" @click="openCreateDialog">新增检查项</el-button>
        </div>
      </div>
    </template>

    <!-- 筛选区 -->
    <el-form inline class="filter-form">
      <el-form-item label="关键词">
        <el-input v-model="filters.keyword" placeholder="名称关键词" clearable style="width: 180px"
          @keyup.enter="search" />
      </el-form-item>
      <el-form-item label="项目类型">
        <el-select v-model="filters.project_type" placeholder="全部" clearable aria-label="按项目类型筛选" style="width: 120px">
          <el-option v-for="(label, value) in TYPE" :key="value" :label="label" :value="value" />
        </el-select>
      </el-form-item>
      <el-form-item label="建设阶段">
        <el-select v-model="filters.phase" placeholder="全部" clearable aria-label="按建设阶段筛选" style="width: 120px">
          <el-option v-for="(label, value) in PHASE" :key="value" :label="label" :value="value" />
        </el-select>
      </el-form-item>
      <el-form-item label="问题类别">
        <el-select v-model="filters.defect_category" placeholder="全部" clearable aria-label="按问题类别筛选" style="width: 130px">
          <el-option v-for="(label, value) in CATEGORY" :key="value" :label="label" :value="value" />
        </el-select>
      </el-form-item>
      <el-form-item label="风险等级">
        <el-select v-model="filters.risk_level" placeholder="全部" clearable aria-label="按风险等级筛选" style="width: 100px">
          <el-option v-for="(label, value) in RISK" :key="value" :label="label" :value="value" />
        </el-select>
      </el-form-item>
      <el-form-item label="状态">
        <el-select v-model="filters.enabled" placeholder="全部" clearable aria-label="按启用状态筛选" style="width: 100px">
          <el-option label="启用" value="true" />
          <el-option label="停用" value="false" />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button type="primary" @click="search">查询</el-button>
        <el-button @click="resetFilters">重置</el-button>
      </el-form-item>
    </el-form>

    <!-- 检查项表格 -->
    <el-table :data="items" v-loading="loading" border stripe>
      <el-table-column prop="name" label="名称" min-width="280" show-overflow-tooltip />
      <el-table-column label="适用类型" min-width="150">
        <template #default="{ row }">
          <el-tag v-for="t in row.applicable_types" :key="t" size="small" class="mini-tag" effect="plain">
            {{ TYPE[t] || t }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="适用阶段" min-width="170">
        <template #default="{ row }">
          <el-tag v-for="p in row.applicable_phases" :key="p" size="small" class="mini-tag" effect="plain">
            {{ PHASE[p] || p }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="问题类别" min-width="110">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ CATEGORY[row.defect_category] || row.defect_category }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="风险等级" min-width="90">
        <template #default="{ row }">
          <el-tag size="small" :type="riskTagType(row.risk_level)">{{ RISK[row.risk_level] || row.risk_level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="启用状态" min-width="90">
        <template #default="{ row }">
          <span>{{row.enabled ? "已启用" : "停用/待发布"}}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="180" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEditDialog(row)">编辑</el-button>
          <KnowledgeVersions :item="row" @changed="loadItems" />
        </template>
      </el-table-column>
    </el-table>

    <div class="pager">
      <el-pagination v-model:current-page="page" :page-size="pageSize" :total="total"
        layout="total, prev, pager, next" @current-change="loadItems" />
    </div>

    <!-- 新增/编辑弹窗 -->
    <el-dialog v-model="dialog.visible" :title="dialog.mode === 'create' ? '新增检查项' : '编辑检查项'" width="680px"
      :close-on-click-modal="false">
      <el-alert v-if="dialog.conflict" :title="dialog.conflict" type="warning" :closable="false" show-icon />
      <el-form :model="dialog.form" :rules="dialog.rules" ref="itemFormRef" label-width="100px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="dialog.form.name" maxlength="100" />
        </el-form-item>
        <el-form-item label="检查要点" prop="pointsValid" class="points-item">
          <div v-for="(p, i) in dialog.form.check_points" :key="i" class="point-row">
            <el-input v-model="dialog.form.check_points[i]" maxlength="200"
              :placeholder="'要点 ' + (i + 1)" />
            <el-button v-if="dialog.form.check_points.length > 1" link type="danger"
              @click="removePoint(i)">删除</el-button>
          </div>
          <el-button link type="primary" @click="addPoint">+ 增加一行</el-button>
        </el-form-item>
        <el-form-item label="依据条款" prop="basis">
          <el-input v-model="dialog.form.basis" type="textarea" :rows="2" maxlength="300" />
        </el-form-item>
        <el-form-item label="问题类别" prop="defect_category">
          <el-select v-model="dialog.form.defect_category" aria-label="问题类别" style="width: 100%">
            <el-option v-for="(label, value) in CATEGORY" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="风险等级" prop="risk_level">
          <el-select v-model="dialog.form.risk_level" aria-label="风险等级" style="width: 100%">
            <el-option v-for="(label, value) in RISK" :key="value" :label="label" :value="value" />
          </el-select>
        </el-form-item>
        <el-form-item label="适用类型" prop="applicable_types">
          <el-checkbox-group v-model="dialog.form.applicable_types">
            <el-checkbox v-for="(label, value) in TYPE" :key="value" :label="value">{{ label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="适用阶段" prop="applicable_phases">
          <el-checkbox-group v-model="dialog.form.applicable_phases">
            <el-checkbox v-for="(label, value) in PHASE" :key="value" :label="value">{{ label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="dialog.saving" @click="saveItem">保存</el-button>
      </template>
    </el-dialog>

    <!-- 批量导入弹窗 -->
    <el-dialog v-model="importDialog.visible" title="批量导入" width="520px">
      <el-upload drag accept=".xlsx" :auto-upload="false" :limit="1" :on-change="onFileChange"
        :on-remove="() => (importDialog.file = null)" :file-list="importDialog.fileList">
        <div class="upload-tip">点击或拖拽 .xlsx 文件到此处<br />请使用标准模板填写</div>
      </el-upload>
      <div class="upload-actions">
        <el-button link type="primary" @click="downloadTemplate">下载模板</el-button>
        <el-button type="primary" :loading="importDialog.uploading" :disabled="!importDialog.file"
          @click="doImport">开始导入</el-button>
      </div>
    </el-dialog>

    <!-- 导入结果弹窗 -->
    <el-dialog v-model="resultDialog.visible" title="导入结果" width="560px">
      <div class="import-summary">
        成功导入 <b class="ok-num">{{ resultDialog.success_count }}</b> 条
        <template v-if="resultDialog.fail_details.length">
          ，失败 <b class="fail-num">{{ resultDialog.fail_details.length }}</b> 条
        </template>
      </div>
      <el-table v-if="resultDialog.fail_details.length" :data="resultDialog.fail_details" border size="small"
        max-height="300">
        <el-table-column prop="row" label="行号" width="80" />
        <el-table-column prop="reason" label="失败原因" />
      </el-table>
      <template #footer>
        <el-button type="primary" @click="resultDialog.visible = false">知道了</el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup>
import KnowledgeVersions from "../../components/KnowledgeVersions.vue";
import { onMounted, reactive, ref } from 'vue';
import { ElMessage } from 'element-plus';
import request from '../../utils/request';
import { TYPE, PHASE, CATEGORY, RISK } from '../../utils/dict';

// ---------- 列表与筛选 ----------
const items = ref([]);
const page = ref(1);
const pageSize = 20;
const total = ref(0);
const loading = ref(false);
const filters = reactive({ keyword: '', project_type: '', phase: '', defect_category: '', risk_level: '', enabled: '' });

function loadItems() {
  loading.value = true;
  const params = { page: page.value, page_size: pageSize, enabled: 'all' };
  if (filters.keyword.trim()) params.keyword = filters.keyword.trim();
  if (filters.project_type) params.project_type = filters.project_type;
  if (filters.phase) params.phase = filters.phase;
  if (filters.defect_category) params.defect_category = filters.defect_category;
  if (filters.risk_level) params.risk_level = filters.risk_level;
  if (filters.enabled) params.enabled = filters.enabled;
  request.get('/api/admin/inspection-items', { params })
    .then((data) => {
      items.value = data.list || [];
      total.value = data.total || 0;
    })
    .catch(() => {})
    .finally(() => {
      loading.value = false;
    });
}

function search() {
  page.value = 1;
  loadItems();
}

function resetFilters() {
  Object.assign(filters, { keyword: '', project_type: '', phase: '', defect_category: '', risk_level: '', enabled: '' });
  search();
}

function riskTagType(risk) {
  return { high: 'danger', medium: 'warning', low: 'success' }[risk] || 'info';
}

// ---------- 新增/编辑 ----------
const itemFormRef = ref(null);
const dialog = reactive({
  visible: false,
  mode: 'create',
  saving: false,
  itemId: null,
  expectedVersion: null,
  originalEnabled: false,
  conflict: '',
  form: { name: '', check_points: [''], basis: '', defect_category: '', risk_level: '', applicable_types: [], applicable_phases: [] },
  rules: {
    name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
    basis: [{ required: true, message: '请输入依据条款', trigger: 'blur' }],
    defect_category: [{ required: true, message: '请选择问题类别', trigger: 'change' }],
    risk_level: [{ required: true, message: '请选择风险等级', trigger: 'change' }],
    applicable_types: [{ required: true, type: 'array', min: 1, message: '请至少选择一个项目类型', trigger: 'change' }],
    applicable_phases: [{ required: true, type: 'array', min: 1, message: '请至少选择一个建设阶段', trigger: 'change' }],
    pointsValid: [{ validator: validatePoints, trigger: 'blur' }],
  },
});

function validatePoints(rule, value, callback) {
  const valid = dialog.form.check_points.some((p) => p && p.trim());
  if (!valid) {
    callback(new Error('请至少填写一条检查要点'));
  } else {
    callback();
  }
}

function addPoint() {
  dialog.form.check_points.push('');
}

function removePoint(i) {
  dialog.form.check_points.splice(i, 1);
}

function openCreateDialog() {
  dialog.conflict = '';
  dialog.expectedVersion = null;
  dialog.mode = 'create';
  dialog.itemId = null;
  dialog.form = { name: '', check_points: [''], basis: '', defect_category: '', risk_level: '', applicable_types: [], applicable_phases: [] };
  dialog.visible = true;
}

function openEditDialog(row) {
  dialog.mode = 'edit';
  dialog.itemId = row.id;
  dialog.expectedVersion = row.version;
  dialog.originalEnabled = row.enabled;
  dialog.conflict = '';
  dialog.form = {
    name: row.name,
    check_points: (row.check_points && row.check_points.length ? row.check_points : ['']).slice(),
    basis: row.basis,
    defect_category: row.defect_category,
    risk_level: row.risk_level,
    applicable_types: row.applicable_types.slice(),
    applicable_phases: row.applicable_phases.slice(),
  };
  dialog.visible = true;
}

function saveItem() {
  itemFormRef.value.validate((valid) => {
    if (!valid || dialog.saving) return;
    dialog.saving = true;
    const body = {
      name: dialog.form.name.trim(),
      // 过滤空行要点
      check_points: dialog.form.check_points.map((p) => p.trim()).filter(Boolean),
      basis: dialog.form.basis.trim(),
      defect_category: dialog.form.defect_category,
      risk_level: dialog.form.risk_level,
      applicable_types: dialog.form.applicable_types,
      applicable_phases: dialog.form.applicable_phases,
      enabled: true,
    };
    // 编辑时保留原启用状态
    if (dialog.mode === 'edit') {
      body.expected_version = dialog.expectedVersion;
      body.enabled = dialog.originalEnabled;
    }
    const url = dialog.mode === 'create' ? '/api/admin/inspection-items' : '/api/admin/inspection-items/' + dialog.itemId;
    const done = () => {
      dialog.visible = false;
      ElMessage.success(dialog.mode === 'create' ? '新增成功' : '已保存');
      loadItems();
    };
    const req = dialog.mode === 'create' ? request.post(url, body) : request.put(url, body);
    req.then(done).catch((error) => {
      if (error.status === 409 || error.code === 409 || error.code === 1014) {
        dialog.conflict = '此检查项已被其他人修改，您的输入已保留。请复制需要保留的内容，关闭并重新打开最新版本后合并保存。';
      }
    }).finally(() => {
      dialog.saving = false;
    });
  });
}

// ---------- 模板下载（blob 方式携带 token） ----------
function downloadTemplate() {
  request.get('/api/admin/inspection-items/template', { responseType: 'blob' }).then((blob) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = '检查项导入模板.xlsx';
    a.click();
    URL.revokeObjectURL(url);
  }).catch(() => {});
}

// ---------- 批量导入 ----------
const importDialog = reactive({ visible: false, file: null, fileList: [], uploading: false });
const resultDialog = reactive({ visible: false, success_count: 0, fail_details: [] });

function onFileChange(file) {
  importDialog.file = file.raw;
  importDialog.fileList = [file];
}

function doImport() {
  if (!importDialog.file || importDialog.uploading) return;
  importDialog.uploading = true;
  const form = new FormData();
  form.append('file', importDialog.file);
  request.post('/api/admin/inspection-items/import', form).then((data) => {
    resultDialog.success_count = data.success_count || 0;
    resultDialog.fail_details = data.fail_details || [];
    resultDialog.visible = true;
    // 导入后刷新列表
    importDialog.visible = false;
    importDialog.file = null;
    importDialog.fileList = [];
    loadItems();
  }).catch(() => {}).finally(() => {
    importDialog.uploading = false;
  });
}

onMounted(loadItems);
</script>

<style scoped>
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.filter-form {
  margin-bottom: 4px;
}
.mini-tag {
  margin: 2px 4px 2px 0;
}
.pager {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
.point-row {
  display: flex;
  gap: 8px;
  margin-bottom: 8px;
  width: 100%;
}
.points-item :deep(.el-form-item__content) {
  display: block;
}
.upload-tip {
  padding: 24px 0;
  color: #5b6b7d;
  line-height: 1.8;
}
.upload-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 12px;
}
.import-summary {
  font-size: 14px;
  margin-bottom: 12px;
}
.ok-num {
  color: #2e7d3a;
  font-size: 18px;
}
.fail-num {
  color: #e5652a;
  font-size: 18px;
}
</style>
