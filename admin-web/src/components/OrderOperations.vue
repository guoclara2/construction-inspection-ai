<template>
  <section>
    <h3>历轮整改与审核</h3>
    <div v-for="r in rounds" :key="r.round" class="round">
      <h4>第 {{r.round}} 轮 · {{r.result || '待审核'}}</h4>
      <p style="white-space:pre-wrap">{{r.text}}</p>
      <p>提交人 {{r.submitter_id || '旧记录未知'}} · {{r.submitted_at}}</p>
      <p>审核人 {{r.reviewer_id || '—'}} · {{r.reviewed_at}} · {{r.comment}}</p>
      <el-image v-for="p in r.photos" :key="p.id" :src="p.signedUrl" :preview-src-list="r.photos.map(x=>x.signedUrl)" style="width:120px;height:90px;margin:4px" />
    </div>
    <h3>延期申请</h3>
    <div v-for="e in extensions" :key="e.id" class="round">
      <p>{{e.deadline}} · {{e.status}} · {{e.reason}}</p><p>{{e.comment}}</p>
      <template v-if="e.status==='pending'"><el-button @click="review(e,true)">批准延期</el-button><el-button @click="review(e,false)">驳回申请</el-button></template>
    </div>
    <h3>原始现场位置与多图证据</h3>
    <p v-if="record.location">{{Object.entries(record.location).filter(x=>x[1]).map(x=>x[0]+': '+x[1]).join('；')}}</p>
    <div v-for="p in photos" :key="p.id" class="round">
      <el-image :src="p.signedUrl" :preview-src-list="photos.map(x=>x.signedUrl)" style="width:160px;height:120px" />
      <p>坐标系 {{p.evidence?.coordinate_system || '未知'}} · 精度 {{p.gps_accuracy ?? '未知'}} 米</p>
      <p>{{p.evidence?.reasons?.join('；')}}</p>
      <p v-for="(r,i) in p.evidence?.reviews || []" :key="i">复核 {{r.user_id}} · {{r.time}} · {{r.comment}}</p>
      <el-button @click="reviewEvidence(p)">记录存证复核</el-button>
    </div>
  </section>
</template>
<script setup>
import {ref,watch} from 'vue';
import {ElMessageBox} from 'element-plus';
import request,{signedImageUrl} from '../utils/request';
const props=defineProps({order:{type:Object,required:true}}),emit=defineEmits(['changed']);
const rounds=ref([]),extensions=ref([]),record=ref({}),photos=ref([]);
let generation=0;
async function signed(list){return Promise.all((list||[]).map(async(p)=>({...p,signedUrl:await signedImageUrl(p.id)})));}
async function load(){
  const seq=++generation,order=props.order;if(!order.id)return;
  const rs=await Promise.all((order.rounds||[]).map(async(r)=>({...r,photos:await signed(r.photos)})));
  const es=await request.get('/api/orders/'+order.id+'/extensions');
  const rec=await request.get('/api/inspection-records/'+order.record_id);
  const ps=await signed(rec.photos);
  if(seq!==generation)return;rounds.value=rs;extensions.value=es;record.value=rec;photos.value=ps;
}
async function review(e,approve){const {value}=await ElMessageBox.prompt('请输入审批意见','延期审批',{inputValidator:v=>!!v?.trim()||'意见必填'});await request.post(`/api/orders/${props.order.id}/extensions/${e.id}/review`,{approve,comment:value,expected_version:props.order.version});emit('changed');}
async function reviewEvidence(p){const {value}=await ElMessageBox.prompt('根据现场核验情况填写说明，原始异常标记将保留','存证复核',{inputValidator:v=>!!v?.trim()||'说明必填'});await request.post('/api/attachments/'+p.id+'/evidence-review',{comment:value});await load();}
watch(()=>[props.order.id,props.order.version],()=>load().catch(()=>{}),{immediate:true});
</script>
<style scoped>.round{border:1px solid #dce3eb;padding:12px;margin:12px 0}p{overflow-wrap:anywhere}</style>
