<template>
  <el-button link @click="open">版本与发布</el-button>
  <el-dialog v-model="visible" title="知识版本及发布审核" width="720px" append-to-body>
    <p>当前版本 {{item.version}} · {{item.publication}}。新建、编辑或导入后为停用草稿，需要另一位知识库管理员审核发布。</p>
    <el-date-picker v-model="from" type="datetime" placeholder="生效时间" />
    <el-date-picker v-model="until" type="datetime" placeholder="失效时间" />
    <el-button type="primary" :disabled="!from || !until" @click="publish">审核并发布当前版本</el-button>
    <div v-for="v in versions" :key="v.version"><h4>版本 {{v.version}} · {{v.created_at}}</h4><p>{{v.snapshot.name}}</p><p>{{v.snapshot.basis}}</p><pre style="white-space:pre-wrap">{{v.snapshot.check_points}}</pre><p>编辑人 {{v.snapshot.updated_by}} · 审核人 {{v.snapshot.approved_by || '未记录'}}</p></div>
  </el-dialog>
</template>
<script setup>
import {ref} from 'vue';import request from '../utils/request';import {ElMessage} from 'element-plus';
const props=defineProps({item:Object}),emit=defineEmits(['changed']);
const visible=ref(false),versions=ref([]),from=ref(null),until=ref(null);
async function open(){versions.value=await request.get('/api/knowledge/'+props.item.id+'/versions');visible.value=true;}
async function publish(){await request.post('/api/knowledge/'+props.item.id+'/publish',{expected_version:props.item.version,effective_from:from.value.toISOString(),effective_until:until.value.toISOString()});visible.value=false;ElMessage.success('已审核发布');emit('changed');}
</script>
