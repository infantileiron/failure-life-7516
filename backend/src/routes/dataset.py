from fastapi import APIRouter,Depends
from src.controllers import dataset as controller
from src.schemas.entities import DatasetInput,RenameDataset,SpecimenBatchInput,WithdrawalBatchInput
from src.services.auth import current,editor,admin
router=APIRouter(prefix="/datasets",tags=["datasets"])
@router.get("")
def list_items(user=Depends(current)): return controller.list_items()
@router.post("",status_code=201)
def create(data:DatasetInput,user=Depends(editor)): return controller.create(data)
@router.get("/{id}")
def detail(id:str,user=Depends(current)): return controller.detail(id)
@router.patch('/{id}')
def rename(id:str,data:RenameDataset,user=Depends(editor)): return controller.rename(id,data)
@router.get("/{id}/versions")
def versions(id:str,user=Depends(current)): return controller.versions(id)
@router.get("/{id}/batches")
def batches(id:str,user=Depends(current)): return controller.batches(id)
@router.post("/{id}/specimens",status_code=201)
def append_specimens(id:str,data:SpecimenBatchInput,user=Depends(editor)): return controller.append_specimens(id,data,user)
@router.post("/{id}/withdrawals",status_code=201)
def withdraw_specimens(id:str,data:WithdrawalBatchInput,user=Depends(editor)): return controller.withdraw_specimens(id,data,user)
