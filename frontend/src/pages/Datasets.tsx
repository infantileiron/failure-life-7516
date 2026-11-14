import {useState} from 'react';
import * as api from '../api/datasets';
import {useResource} from '../hooks/useResource';
import {refreshResources} from '../stores/resources';
import PageTitle from '../components/common/PageTitle';
import Notice from '../components/common/Notice';
import BatchLedger from '../components/BatchLedger';
import type {User} from '../types/entities';

export default function Datasets({user}:{user:User}){
  const{data,error}=useResource(api.list,[]);
  const[selected,setSelected]=useState('');
  const[ledgerKey,setLedgerKey]=useState(0);
  const[name,setName]=useState('补充材料试件观察');
  const[source,setSource]=useState('material_life_202611.csv');
  const[csv,setCsv]=useState('');
  const[message,setMessage]=useState('');
  const[revision,setRevision]=useState('');
  const[batchSource,setBatchSource]=useState('');
  const[batchCsv,setBatchCsv]=useState('');
  const[note,setNote]=useState('');
  const[withdrawIds,setWithdrawIds]=useState('');
  const[withdrawRevision,setWithdrawRevision]=useState('');
  const current=data.find(d=>d.id===selected);
  const nextRevision=current?current.latest_revision+1:1;
  const done=(result:any)=>{
    setMessage(result.replay
      ?`修订 ${result.revision} 为同版同内容重放，已忽略，未生成新版本、未重复累计`
      :`修订 ${result.revision} 已生效 → 第 ${result.resulting_version} 版：写入 ${result.counts.written}，忽略 ${result.counts.ignored}，撤回 ${result.counts.withdrawn}`);
    setBatchCsv('');setWithdrawIds('');setNote('');
    refreshResources();setLedgerKey(k=>k+1);
  };
  return <>
    <PageTitle chapter="02" title="观察时间与失效状态，分批补传" description="event=1表示已记录失效，event=0表示右删失；若同时提供censored，必须等于1−event。整表导入仍是新建资料；已有资料按试件编号与递增修订号增量合并，撤回保留版本。"/>
    <Notice error={error||message}/>
    <section className="paper table-scroll">
      <table><thead><tr><th>试件文件</th><th>试件数</th><th>组别</th><th>失效 / 删失</th><th>最新版本 / 修订</th><th>批次 / 撤回</th></tr></thead>
        <tbody>{data.map(d=><tr key={d.id} className="clickable" onClick={()=>setSelected(d.id)}>
          <td><b>{d.name}</b><small>{d.source}</small></td><td>{d.row_count}</td><td>{d.groups.join(' / ')}</td><td>{d.events} / {d.censored}</td>
          <td>第 {d.latest_version} 版<small>修订号 {d.latest_revision}</small></td>
          <td>{d.batch_count} 批<small>撤回 {d.withdrawn_count} 件</small></td>
        </tr>)}</tbody></table>
    </section>

    {current&&<>
      <section className="paper">
        <h2>资料版本 · {current.name}</h2>
        <p>新运行默认使用 <b>第 {current.latest_version} 版</b>；旧版本快照继续保留，可在生存研究中指定旧版本继续分析。资料校验和 <small>{current.data_checksum}</small></p>
        {user.role!=='viewer'&&<div className="version-forms">
          <form onSubmit={async e=>{e.preventDefault();try{done(await api.appendSpecimens(current.id,{revision:+revision||nextRevision,source:batchSource||source,note,csv_text:batchCsv}))}catch(e:any){setMessage(e.message)}}}>
            <h3>补传试件批次</h3>
            <div className="form-grid">
              <label>递增修订号<input aria-label="补传修订号" type="number" min="1" value={revision} placeholder={String(nextRevision)} onChange={e=>setRevision(e.target.value)}/></label>
              <label>来源文件<input aria-label="补传来源" value={batchSource} onChange={e=>setBatchSource(e.target.value)} placeholder={source}/></label>
              <label className="wide">试件内容<textarea aria-label="补传试件内容" value={batchCsv} onChange={e=>setBatchCsv(e.target.value)} placeholder="specimen_id,group,duration_hours,event,censored"/></label>
              <label className="wide">批次说明<input aria-label="补传说明" value={note} onChange={e=>setNote(e.target.value)}/></label>
            </div>
            <button className="primary">合并试件批次</button>
          </form>
          <form onSubmit={async e=>{e.preventDefault();try{
            const ids=withdrawIds.split(/[,\s]+/).map(x=>x.trim()).filter(Boolean);
            done(await api.withdraw(current.id,{revision:+withdrawRevision||nextRevision,source:'withdrawals',note:note||'撤回试件',specimen_ids:ids}));
          }catch(e:any){setMessage(e.message)}}}>
            <h3>撤回试件</h3>
            <div className="form-grid">
              <label>递增修订号<input aria-label="撤回修订号" type="number" min="1" value={withdrawRevision} placeholder={String(nextRevision)} onChange={e=>setWithdrawRevision(e.target.value)}/></label>
              <label className="wide">试件编号（逗号或换行分隔）<textarea aria-label="撤回试件编号" value={withdrawIds} onChange={e=>setWithdrawIds(e.target.value)} placeholder="LC-A-001, LC-B-002"/></label>
            </div>
            <button>撤回并保留版本</button>
          </form>
        </div>}
        <div className="table-scroll"><table>
          <thead><tr><th>版本</th><th>修订</th><th>试件数</th><th>失效 / 删失</th><th>撤回</th><th>来源</th><th>状态</th></tr></thead>
          <tbody>{(current.versions||[]).map(v=><tr key={v.id}>
            <td>第 {v.version} 版<small>{v.id}</small></td><td>{v.revision}</td><td>{v.row_count}</td><td>{v.events} / {v.censored}</td>
            <td>{v.withdrawn_count}</td><td>{v.source||'—'}<small>{v.batch_id||'整表导入'}</small></td><td>{v.is_latest?'当前最新':'历史快照'}</td>
          </tr>)}</tbody>
        </table></div>
      </section>
      <BatchLedger key={current.id+ledgerKey} dataset={current.id}/>
    </>}

    {user.role!=='viewer'&&<form className="paper form-grid" onSubmit={async e=>{e.preventDefault();try{await api.create({name,source,csv_text:csv});setMessage('');setCsv('');refreshResources()}catch(e:any){setMessage(e.message)}}}>
      <h2>新建资料（整表导入）</h2>
      <label>数据集名称<input aria-label="数据集名称" value={name} onChange={e=>setName(e.target.value)}/></label>
      <label>选择CSV<input type="file" accept=".csv" onChange={async e=>{const f=e.target.files?.[0];if(f){if(f.size>1000000){setMessage('CSV不能超过1MB');return}setSource(f.name);setCsv(await f.text())}}}/></label>
      <label className="wide">试件内容<textarea value={csv} onChange={e=>setCsv(e.target.value)} placeholder="specimen_id,group,duration_hours,event,censored"/></label>
      <button className="primary">校验并导入试件</button>
    </form>}
  </>;
}
