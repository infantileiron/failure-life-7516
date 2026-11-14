from dataclasses import dataclass,field
from typing import Any
@dataclass
class SpecimenDataset:
 id:str
 name:str
 source:str
 rows:list[dict]
 created_at:str=''
 latest_version:int=1
 latest_version_id:str=''
 latest_revision:int=0
 version_count:int=1
 batch_count:int=0
 withdrawn_count:int=0
@dataclass
class DatasetVersion:
 id:str
 dataset_id:str
 version:int
 revision:int
 rows:list[dict]
 specimen_revisions:dict[str,int]
 withdrawn:list[dict]
 source:str
 batch_id:str
 checksum:str
 created_at:str
@dataclass
class DatasetBatch:
 id:str
 dataset_id:str
 revision:int
 content_hash:str
 status:str
 kind:str
 source:str
 note:str
 actor:str
 counts:dict[str,int]
 details:list[dict]
 resulting_version:int
 dataset_version_id:str
 replay_count:int
 created_at:str
@dataclass
class CensoringRule:
 id:str
 name:str
 cutoff_hours:float|None
 note:str=''
 created_at:str=''
@dataclass
class SurvivalStudy:
 id:str
 name:str
 dataset_id:str
 rule_id:str
 group_a:str='A'
 group_b:str='B'
 created_at:str=''
@dataclass
class SurvivalRun:
 id:str
 study_id:str
 status:str='queued'
 result:dict[str,Any]=field(default_factory=dict)
 error:str=''
 created_at:str=''
 dataset_id:str=''
 dataset_version_id:str=''
 dataset_version:int=0
 dataset_revision:int=0
 dataset_checksum:str=''
@dataclass
class LifeReview:
 id:str
 run_id:str
 comment:str
 status:str='pending'
 reviewer:str=''
 created_at:str=''
@dataclass
class Report:
 id:str
 run_id:str
 review_id:str
 title:str
 created_at:str=''
