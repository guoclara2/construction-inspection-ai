// 日期显示工具：统一简写 MM-DD HH:mm
function formatTime(str) {
  if (!str) {
    return '';
  }
  // 入参格式 YYYY-MM-DD HH:MM:SS
  const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})/.exec(String(str));
  if (!m) {
    return str;
  }
  return m[2] + '-' + m[3] + ' ' + m[4] + ':' + m[5];
}

// 附件存证信息展示（P04 §2.5.5）：拍摄时间 / 坐标 / 来源 / 是否存证异常
function evidenceMeta(att) {
  if (!att) {
    return null;
  }
  const hasGps = att.gps_lat !== null && att.gps_lat !== undefined
    && att.gps_lng !== null && att.gps_lng !== undefined;
  const sourceLabel = att.source === 'camera' ? '现场拍摄' : (att.source === 'album' ? '相册选取' : '来源未知');
  return {
    exifTime: att.exif_time || '无拍摄时间',
    coord: hasGps ? (att.gps_lat + ', ' + att.gps_lng) : '无定位',
    sourceLabel: sourceLabel,
    suspicious: att.suspicious === true,
  };
}

// 存证异常具体项（P1-4）：纯前端按既有字段推导，口径与后端上传时判定一致
function evidenceIssues(att) {
  if (!att) {
    return [];
  }
  if (att.evidence && Array.isArray(att.evidence.reasons)) return att.evidence.reasons;
  const issues = [];
  if (!att.exif_time) {
    issues.push('无拍摄时间');
  } else if (att.created_at
    && Math.abs(new Date(att.created_at.replace(' ', 'T')) - new Date(att.exif_time.replace(' ', 'T'))) > 2 * 3600 * 1000) {
    issues.push('拍摄时间与上传时间相差过大');
  }
  const hasGps = att.gps_lat !== null && att.gps_lat !== undefined
    && att.gps_lng !== null && att.gps_lng !== undefined;
  if (!hasGps) {
    issues.push('无定位信息');
  }
  if (att.source === 'album') {
    issues.push('相册选取（非现场拍摄）');
  }
  return issues;
}

module.exports = {
  formatTime: formatTime,
  evidenceMeta: evidenceMeta,
  evidenceIssues: evidenceIssues,
};
