from django.contrib import admin
from .models import (
    EscalationWorkflow, EscalationStep, GrievanceType, GrievanceCategory, GrievanceSubCategory,
    GrievanceFlag, GrievanceChannel
)

class EscalationStepInline(admin.TabularInline):
    model = EscalationStep
    extra = 0

@admin.register(EscalationWorkflow)
class EscalationWorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "is_sensitive", "category_slug", "active")
    list_filter = ("is_sensitive", "active")
    search_fields = ("name", "category_slug")
    inlines = [EscalationStepInline]


class GrievanceSubCategoryInline(admin.TabularInline):
    model = GrievanceSubCategory
    extra = 0
    fields = ("code", "name", "order", "active")
    show_change_link = True

@admin.register(GrievanceChannel)
class GrievanceChannelAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "active", "order")
    list_filter = ("active",)
    search_fields = ("code", "name")
    ordering = ("order", "name")

@admin.register(GrievanceFlag)
class GrievanceFlagAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "active", "order")
    list_filter = ("active",)
    search_fields = ("code", "name")
    ordering = ("order", "name")

@admin.register(GrievanceCategory)
class GrievanceCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "parent", "active", "order")
    list_filter = ("active", "parent__is_sensitive", "parent")
    search_fields = ("code", "name", "parent__code", "parent__name")
    ordering = ("parent__order", "order", "name")
    inlines = [GrievanceSubCategoryInline]


class GrievanceCategoryInline(admin.TabularInline):
    model = GrievanceCategory
    extra = 0
    fields = ("code", "name", "order", "active")
    show_change_link = True


@admin.register(GrievanceType)
class GrievanceTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_sensitive", "active", "order")
    list_filter = ("is_sensitive", "active")
    search_fields = ("code", "name")
    ordering = ("order", "name")
    inlines = [GrievanceCategoryInline]
