from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth.dependencies import csrf_protect, get_current_user, require_admin
from app.database import get_db
from app.models import IngredientDemandSetting, User
from app.schemas.demand import DemandSettingsOut, DemandSettingsUpdate
from app.services import demand

router = APIRouter()


@router.get("/ingredients/{ingredient_id}/demand-settings", response_model=DemandSettingsOut)
def get_settings(ingredient_id: int, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    ingredient = demand.get_ingredient(db, ingredient_id)
    return demand.settings_view(ingredient, db.get(IngredientDemandSetting, ingredient_id))


@router.patch("/ingredients/{ingredient_id}/demand-settings", response_model=DemandSettingsOut,
              dependencies=[Depends(csrf_protect)])
def update_settings(ingredient_id: int, payload: DemandSettingsUpdate,
                    admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return demand.update_settings(db, ingredient_id=ingredient_id, admin=admin, data=payload)
