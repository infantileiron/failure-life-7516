import {useState} from 'react';
import * as api from '../api/datasets';
import type {DatasetBatch} from '../types/entities';
import {useResource} from '../hooks/useResource';
import Notice from './common/Notice';

export const reasonLabels:Record<string,string>={
  new:'新增写入',corrected:'修订写入',unchanged:'同内容忽略',withdrawn_stale:'撤回后旧记录，不复活',
  readded:'实质修订重新纳入',already_withdrawn:'重复撤回忽略',withdrawn:'撤回',
};
export const actionLabels:Record<string,string>={written:'写入',ignored:'忽略',withdrawn:'撤回'};

export default function BatchLedger({dataset}:{dataset:string}){
  const{data,error}=useResource(()=>api.batches(dataset),[],dataset);
  const[open,setOpen]=useState<string>('');
  const totals=data.reduce((a,b)=>({written:a.written+b.counts.written,ignored:a.ignored+b.counts.ignored,withdrawn:a.withdrawn+b.counts.withdrawn}),{written:0,ignored:0,withdrawn:0});
  const selected:DatasetBatch|undefined=data.find(x=>x.id===open);
  return <section className="paper batch-ledger">
    <div className="batch-head"><h2>分批补传与撤回明细</h2><div className="batch-totals"><span>累计写入 <b>{totals.written}</b></span><span>忽略 <b>{totals.ignored}</b></span><span>撤回 <b>{totals.withdrawn}</b></span></div></div>
    <Notice error={error}/>
    {data.length===0?<p className="batch-empty">尚无补传批次。整表导入即为资料的修订号 0 / 第 1 版。</p>:<><p className="batch-hint">点击任一批次行查看逐件写入 / 忽略 / 撤回明细。重放批次不新增版本，计数不会重复累计。</p><div className="table-scroll"><table><thead><tr><th>修订号</th><th>类型</th><th>写入</th><th>忽略</th><th>撤回</th><th>结果版本</th><th>重放</th><th>来源 / 说明</th></tr></thead><tbody>
      {data.map(b=><tr key={b.id} className={open===b.id?'expanded clickable':'clickable'} onClick={()=>setOpen(open===b.id?'':b.id)}>
        <td><b>{b.revision}</b></td>
        <td>{b.kind==='withdrawal'?'撤回':'补传'}</td>
        <td>{b.counts.written}</td><td>{b.counts.ignored}</td><td>{b.counts.withdrawn}</td>
        <td>v{b.resulting_version}</td>
        <td>{b.replay_count?`重放 ${b.replay_count} 次（未重复累计）`:'—'}</td>
        <td>{b.source||'—'}<small>{b.note||b.actor}</small></td>
      </tr>)}
    </tbody></table></div></>}
    {selected&&<div className="batch-details"><h3>修订 {selected.revision} 逐件明细</h3><div className="table-scroll"><table><thead><tr><th>试件编号</th><th>动作</th><th>原因</th></tr></thead><tbody>{selected.details.map(d=><tr key={d.specimen_id}><td>{d.specimen_id}</td><td>{actionLabels[d.action]||d.action}</td><td>{reasonLabels[d.reason]||d.reason}</td></tr>)}</tbody></table></div></div>}
  </section>;
}
